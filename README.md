# PONTA reduction to HDF5

Manyo/UtsusemiでPONTAのイベントを64 × 64のカウント配列に変換し、DATの測定値・ヘッダー情報とともにHDF5へ保存します。

## 必要なもの

- リダクション：WLSF32変換器を含むManyo/Utsusemi、NumPy、h5py。
- 表示：通常のPython、NumPy、h5py、Matplotlib。表示にはUtsusemi不要。
- 対象DAT、対応するRunのイベントフォルダ全体（EDB・T0B）、測定時のreadout/detector XML。

測定データ・XML・Utsusemi本体はこのリポジトリに含めません。XMLは測定環境から別途取得し、`instrument/`に配置してください。イベントは`DAQ_DATA/WLS/対象Runフォルダ/`に配置します。

## リダクション

1. `config.example.ini`を`config.ini`へコピーします。
2. DAT一覧、Run番号、DAQ開始日時、イベント基点、XML、出力名を設定します。例のRun 742・日時は自分の測定に置き換えてください。パスはINIのあるフォルダが基準です。ファイル一覧の行頭には空白が必要で、`;`の行は無効です。
3. macOSではUtsusemiを手動で起動し、同じターミナルで実行します。他OSは導入先の起動方法に従ってください。

```sh
source /Applications/Utsusemi/.opt/bin/_loadenv
python3 -c "import sys; import Manyo.Utsusemi; import h5py; import numpy; print(sys.executable)"
python3 reduction_to_hdf5.py config.ini
```

Utsusemiはpipでは導入できません。`requirements.txt`は主に表示用です。Utsusemi側の既存NumPyを一括更新しないでください。

DATの開始・終了日時からDAQ開始を引いた秒数でtime sliceします。測定時間は終了−開始の経過秒数で、DATのtime列とは別です。各DATにつき1枚の64 × 64配列を保存します。同じスキャンの既存結果は上書きできないため、再計算時は新しい出力名を指定します。

## 保存構造

```text
/config/config_ini       INI本文
/config/readout_xml      XML本文
/config/detector_xml     XML本文
/scans/scan_0048         dataset: counts[y, x], shape (64, 64)
/scans/scan_0049         dataset: counts[y, x], shape (64, 64)
```

各scan datasetの属性にDAT各列、ヘッダー、run_number、measurement_time_seconds等を保存します。本体は未規格化のcountsです。

## 表示・PNG保存

表示用の通常Pythonで次を実行します。

```sh
python3 -m venv .venv_plot
source .venv_plot/bin/activate
python3 -m pip install -r requirements.txt
python3 read_hdf5.py Scan0048-0049.h5 --scan 48
```

`--scan 48`はその番号だけを一覧表示してPNG保存し、操作可能なグラフを開きます。GUI環境が必要です。番号なしでは全スキャンを一覧表示し、PNGを保存して図を閉じます。`--output-dir`で保存先を指定できます。

PNGはcountsをmeasurement_time_secondsで割ったcounts/sです。タイトルにc2を表示します。色の上限は図ごとに自動設定されます。HDF5は変更しません。

## エラー時

- Manyoが見つからない：Utsusemi環境と実際のPythonパスを確認。
- 時間区間が不正：DAT・Run番号・DAQ開始日時の対応を確認。
- reduction already exists：出力名を変更。
- イベントが見つからない：イベント基点、Runフォルダ、EDB・T0Bのコピーを確認。
- Utsusemiの診断ログ：完了パスとカウントを確認し、異常が続く場合はログを保存して担当者へ確認。

## 詳細マニュアル

[環境準備・設定・SSLエラー対処を含む手順書](https://docs.google.com/document/d/1aSdkF12Noq1oRS8OzYlhMxLqFlWpYtVgXN26NvQyeaA/edit)

マニュアルの閲覧権限はGoogleドキュメント側の共有設定に従います。
