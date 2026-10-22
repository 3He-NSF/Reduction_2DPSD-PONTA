#!/usr/bin/env python3
"""Save 64x64 PONTA counts with DAT columns and metadata as attributes.

Run in the Manyo/Utsusemi Python environment:
    python3 reduction_to_hdf5.py configreduction_to_hdf5.ini
"""
import argparse
import configparser
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import h5py
import numpy as np


def read_scan(path, encoding):
    """Parse fixed-width DAT fields without shifting blank columns."""
    raw = path.read_bytes()
    lines = raw.decode(encoding).splitlines()
    metadata = {}
    header = None
    rows = []
    for line_number, line in enumerate(lines, 1):
        if line.startswith('#'):
            text = line[1:].strip()
            if '=' in text:
                key, value = text.split('=', 1)
                key = key.strip()
                metadata['start_time' if key == 'time' else key] = value.strip()
            completed = re.fullmatch(
                r'(\d{2}:\d{2}:\d{2})\s+(\d{4}/\d{2}/\d{2})\s+scan completed\.', text
            )
            if completed:
                metadata['end_time'] = completed.group(1)
                metadata['end_date'] = completed.group(2)
            if text.startswith('Pt.'):
                if header is not None:
                    raise ValueError(f'{path}:{line_number}: duplicate column header')
                header = list(re.finditer(r'\S+', line[1:]))
                # Token ends are the right edges of the fixed-width fields.
                names = [m.group() for m in header]
                ends = [m.end() + 1 for m in header]
                if len(set(names)) != len(names) or any('/' in n for n in names):
                    raise ValueError('Column names must be unique and contain no slash')
            continue
        if not line.strip():
            continue
        if header is None:
            raise ValueError(f'{path}:{line_number}: data before column header')
        if line[ends[-1]:].strip():
            raise ValueError(f'{path}:{line_number}: unexpected data after last column')
        values = []
        start = 0
        for name, end in zip(names, ends):
            field = line[start:end].strip()
            try:
                values.append(float(field) if field else np.nan)
            except ValueError as exc:
                raise ValueError(f'{path}:{line_number}: invalid value for {name}: {field!r}') from exc
            start = end
        rows.append(values)
    if header is None or not rows:
        raise ValueError(f'{path}: missing column header or data')
    for time_key, date_key in [('start_time', 'date'), ('end_time', 'end_date')]:
        if time_key in metadata:
            if not metadata.get(date_key):
                raise ValueError(f'{path}: missing {date_key} for {time_key}')
            metadata[time_key] = datetime.strptime(
                f'{metadata[date_key]} {metadata[time_key]}', '%Y/%m/%d %H:%M:%S'
            ).isoformat(sep=' ')
    for name in ('date', 'col_headers', 'end_date'):
        metadata.pop(name, None)
    data = np.asarray(rows, dtype=np.float64)
    if 'number of pts' in metadata and int(metadata['number of pts']) != len(data):
        raise ValueError(f'{path}: declared point count differs from actual row count')
    points = data[:, names.index('Pt.')]
    if not np.array_equal(points, np.arange(1, len(data) + 1)):
        raise ValueError(f'{path}: missing or out-of-order point numbers')
    return raw, metadata, names, data


def reduce_events(paths, run, start_sec, end_sec):
    """Slice events by DAQ elapsed seconds and return counts indexed [y, x]."""
    nx, ny = 64, 64
    # Same Utsusemi API and time slicing as data_reduction_ST20250701.py.
    try:
        import Manyo
        import Manyo.Utsusemi as mu
    except ImportError as exc:
        raise RuntimeError('Manyo/Utsusemi is unavailable. Run this script in the J-PARC Utsusemi Python environment.') from exc
    converter = mu.UtsusemiEventDataConverterWLSF32()
    converter.LoadParamFiles(str(paths['readout_file']), str(paths['detector_file']))
    converter.SetHistAllocation()
    converter.SetRangeOfSingleTimeSlicing(start_sec, end_sec)
    matrix = Manyo.ElementContainerMatrix()
    converter.SetElementContainerMatrix(matrix, run, str(paths['data_path']), '')
    image = np.empty((ny, nx), dtype=np.float64)
    visited = set()
    for ix in range(nx):
        for iy in range(ny):
            header = matrix.Put(ix).Put(iy).PutHeader()
            x, y = int(header.PutInt4('XPOS')), int(header.PutInt4('YPOS'))
            count = float(header.PutDouble('TotalCounts'))
            if not (0 <= x < nx and 0 <= y < ny) or (x, y) in visited:
                raise ValueError('Utsusemi returned invalid or duplicate pixel coordinates')
            if not np.isfinite(count) or count < 0:
                raise ValueError('Utsusemi returned invalid counts')
            visited.add((x, y))
            image[y, x] = count
    return image


def load_settings(config_path):
    """Read INI settings once and resolve paths relative to the INI file."""
    config_path = Path(config_path).expanduser().resolve()
    config_text = config_path.read_text(encoding='utf-8-sig')
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_text)
    base = config_path.parent
    sources = [
        (base / Path(line.strip()).expanduser()).resolve()
        for line in config['FILES']['scan_files'].splitlines()
        if line.strip()
    ]
    if not sources or len(set(sources)) != len(sources):
        raise ValueError('FILES.scan_files must contain unique input paths')
    settings = config['REDUCTION']
    run_number = settings.getint('run_number', fallback=-1)
    if run_number < 0:
        raise ValueError('REDUCTION.run_number must be a nonnegative integer')
    try:
        daq_start = datetime.strptime(
            config['STARTTIME']['start_time'].strip().replace('/', '-'),
            '%Y-%m-%d %H:%M:%S',
        )
    except ValueError as exc:
        raise ValueError('STARTTIME.start_time must be YYYY-MM-DD HH:MM:SS') from exc
    paths = {
        name: (base / Path(settings[name]).expanduser()).resolve()
        for name in ('data_path', 'readout_file', 'detector_file')
    }
    output = (base / Path(config['OUTPUTFILE']['output_file']).expanduser()).resolve()
    inputs = {config_path, *sources, paths['readout_file'], paths['detector_file']}
    if output in inputs:
        raise ValueError('Output must differ from the input and configuration files')
    return (
        config_text, sources, output, run_number, daq_start, paths,
        config['FILES'].get('encoding', 'utf-8-sig'),
    )


def write_scan(h5, key, counts, metadata, columns, run_number, seconds):
    """Write the count array directly; retain original DAT values as attributes."""
    counts = np.asarray(counts, dtype=np.float64)
    if counts.shape != (64, 64):
        raise ValueError(f'{key}: expected a 64x64 count array')
    if not np.isfinite(counts).all() or (counts < 0).any():
        raise ValueError(f'{key}: counts must be finite and nonnegative')
    attributes = dict(metadata)
    attributes.update(
        run_number=run_number,
        measurement_time_seconds=float(seconds),
        units='counts',
        axis_order='y,x',
    )
    conflicts = set(attributes).intersection(columns)
    if conflicts:
        raise ValueError(f'{key}: DAT columns conflict with metadata: {sorted(conflicts)}')
    scans = h5.require_group('scans')
    if key in scans:
        # Only matching DAT-only records reach this point; edits are in a temp file.
        del scans[key]
    dataset = scans.create_dataset(
        key, data=counts, compression='gzip', shuffle=True, track_order=True,
    )
    dataset.attrs.update(attributes)
    dataset.attrs.update(columns)


def run(config_path):
    """Validate inputs, reduce events, and publish a completed HDF5 archive."""
    (
        config_text, sources, output, run_number, daq_start, paths, encoding,
    ) = load_settings(config_path)
    plans = []
    seen = set()
    for source in sources:
        raw, metadata, names, values = read_scan(source, encoding)
        key = f"scan_{int(metadata['scan']):04d}"
        if key in seen:
            raise ValueError(f'Duplicate scan number: {key}')
        seen.add(key)
        if 'start_time' not in metadata or 'end_time' not in metadata:
            raise ValueError(f'{source}: scan start or completion time is missing')
        start_sec = (datetime.fromisoformat(metadata['start_time']) - daq_start).total_seconds()
        end_sec = (datetime.fromisoformat(metadata['end_time']) - daq_start).total_seconds()
        if start_sec < 0 or end_sec <= start_sec:
            raise ValueError(
                f'{source}: scan interval {start_sec:g}–{end_sec:g} s is invalid '
                f'relative to DAQ start {daq_start}'
            )
        metadata.update(source_file=str(source), sha256=hashlib.sha256(raw).hexdigest())
        reserved = set(metadata) | {
            'run_number', 'measurement_time_seconds', 'units', 'axis_order',
        }
        if reserved.intersection(names):
            raise ValueError(f'{source}: DAT column name conflicts with metadata')
        columns = {name: values[:, i] for i, name in enumerate(names)}
        plans.append((key, metadata, columns, start_sec, end_sec))

    texts = {
        'config_ini': config_text,
        'readout_xml': paths['readout_file'].read_bytes().decode('utf-8-sig'),
        'detector_xml': paths['detector_file'].read_bytes().decode('utf-8-sig'),
    }
    existing = output.exists()
    before = output.stat() if existing else None
    if existing:
        with h5py.File(output, 'r') as h5:
            if (
                h5.attrs.get('format') != 'PONTA scan archive'
                or h5.attrs.get('format_version') != 6
                or 'configurations' in h5
            ):
                raise ValueError('Unsupported archive layout; choose a new output file')
            for key, metadata, columns, start_sec, end_sec in plans:
                if 'scans' in h5 and key in h5['scans']:
                    scan = h5['scans'][key]
                    if not isinstance(scan, h5py.Dataset):
                        raise ValueError(f'{key}: existing record is not a dataset')
                    if scan.attrs.get('sha256') != metadata['sha256']:
                        raise ValueError(f'{key}: existing scan is from a different source')
                    if not scan.dtype.names:
                        raise ValueError(f'{key}: reduction already exists; choose another output file')
            if 'config' in h5:
                for name, text in texts.items():
                    if name not in h5['config'] or h5['config'][name].asstr()[()] != text:
                        raise ValueError('Output contains different settings; choose another output file')

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reduction-', suffix='.h5', dir=output.parent)
    os.close(fd)
    try:
        if existing:
            shutil.copy2(output, temporary)
        with h5py.File(temporary, 'r+' if existing else 'w') as h5:
            h5.attrs['format'] = 'PONTA scan archive'
            h5.attrs['format_version'] = 6
            if 'config' not in h5:
                group = h5.create_group('config')
                group.attrs['sha256'] = hashlib.sha256(
                    json.dumps(texts, ensure_ascii=False, sort_keys=True).encode('utf-8')
                ).hexdigest()
                for name, text in texts.items():
                    group.create_dataset(name, data=text, dtype=h5py.string_dtype('utf-8'))
            for key, metadata, columns, start_sec, end_sec in plans:
                counts = reduce_events(paths, run_number, start_sec, end_sec)
                write_scan(
                    h5, key, counts, metadata, columns, run_number, end_sec - start_sec,
                )
                print(f'{key}: {start_sec:g}–{end_sec:g} s, counts={counts.sum():g}')
        if existing:
            now = output.stat()
            if (now.st_ino, now.st_size, now.st_mtime_ns) != (
                before.st_ino, before.st_size, before.st_mtime_ns,
            ):
                raise RuntimeError('Output changed during reduction; update cancelled')
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path, help='INI configuration file')
    args = parser.parse_args()
    try:
        print(run(args.config))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, configparser.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')
