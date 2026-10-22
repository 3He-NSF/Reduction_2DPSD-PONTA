#!/usr/bin/env python3
"""Plot PONTA 64x64 count-rate maps from HDF5 as PNG files.

    python3 read_hdf5.py Scan0048-0088.h5 --scan 48
    python3 read_hdf5.py Scan0048-0088.h5

With --scan: show only that scan and keep its plot window open.
Without --scan: save PNG files for all scans and close each figure.
Normalization: counts / measurement_time_seconds (not the DAT time column).
"""
import argparse
from pathlib import Path
import re

import h5py
import matplotlib
import numpy as np

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='HDF5 input file')
    parser.add_argument('--scan', type=int, help='Scan number; omit to plot all scans')
    parser.add_argument('--output-dir', type=Path, help='Directory for PNG files')
    args = parser.parse_args()
    output_dir = args.output_dir or args.file.parent / f'{args.file.stem}_heatmaps'
    if args.scan is None:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    try:
        with h5py.File(args.file, 'r') as h5:
            if 'scans' not in h5:
                raise ValueError('The HDF5 file has no /scans group')
            all_keys = sorted(
                (name for name in h5['scans'] if re.fullmatch(r'scan_\d+', name)),
                key=lambda name: int(name.removeprefix('scan_')),
            )
            if args.scan is not None:
                if args.scan < 0:
                    raise ValueError('Scan number must be nonnegative')
                keys = [f'scan_{args.scan:04d}']
                if keys[0] not in h5['scans']:
                    raise ValueError(f'{keys[0]} is not present in the file')
            else:
                keys = all_keys
            if not keys:
                raise ValueError('No scans found')

            print(f'Scans: {len(keys)}')
            print(f'{"Scan":>6}  {"Run":>6}  {"Time [s]":>10}  {"Total counts":>14}  c2')
            for key in keys:
                scan = h5['scans'][key]
                if not isinstance(scan, h5py.Dataset) or scan.shape != (64, 64) or scan.dtype.names:
                    raise ValueError(f'{key} is not a reduced 64x64 count array')
                number = int(key.removeprefix('scan_'))
                run = str(scan.attrs.get('run_number', '-'))
                time = str(scan.attrs.get('measurement_time_seconds', '-'))
                total = np.sum(scan[:], dtype=np.float64)
                c2 = ', '.join(
                    str(value) for value in np.asarray(scan.attrs.get('c2', [])).ravel()
                ) or '-'
                print(f'{number:6d}  {run:>6}  {time:>10}  {total:14g}  {c2}')

            for key in keys:
                if key not in h5['scans']:
                    raise ValueError(f'{key} is not present in the file')
                scan = h5['scans'][key]
                if not isinstance(scan, h5py.Dataset) or scan.shape != (64, 64) or scan.dtype.names:
                    raise ValueError(f'{key} is not a reduced 64x64 count array')
                counts = scan[:].astype(np.float64)
                seconds = float(scan.attrs['measurement_time_seconds'])
                if not np.isfinite(seconds) or seconds <= 0:
                    raise ValueError(f'{key}: measurement_time_seconds must be positive and finite')
                if not np.isfinite(counts).all() or (counts < 0).any():
                    raise ValueError(f'{key}: counts must be finite and nonnegative')
                count_rate = counts / seconds
                c2 = ', '.join(
                    str(value) for value in np.asarray(scan.attrs.get('c2', [])).ravel()
                ) or '-'

                output_dir.mkdir(parents=True, exist_ok=True)
                fig, ax = plt.subplots(figsize=(6.4, 5.4), layout='constrained')
                try:
                    heatmap = ax.imshow(
                        count_rate, origin='lower', interpolation='nearest',
                        cmap='viridis', vmin=0, aspect='equal',
                    )
                    ax.set_xlabel('Pixel X')
                    ax.set_ylabel('Pixel Y')
                    ax.set_title(f'{key}, c2 = {c2}°')
                    fig.colorbar(heatmap, ax=ax, label='Count rate (counts/s)')
                    output = output_dir / f'{key}.png'
                    fig.savefig(output, dpi=200)
                except Exception:
                    plt.close(fig)
                    raise
                if args.scan is None:
                    plt.close(fig)
            print(f'PNG output: {output_dir} ({len(keys)} files)')
        if args.scan is not None:
            plt.show(block=True)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
