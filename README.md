# RobStride RS02 モーター設定ツール（日本語版）

Windows 10/11 x64、Python 3.11、PySide6、InnoMaker USB2CAN-X2向けのデスクトップMVPです。
標準の通信条件は **1,000,000 bit/s・Classical CAN・29ビット拡張ID** です。
公式RobStride Python SDKを基に通信層を分離し、Windowsではpython-canのgs_usb、
Linuxでは任意でSocketCANを使用します。

**起動時は未接続・シミュレーションモードです。モーターを自動で有効化しません。**
クラウドにはUSB2CAN-X2／RS02がなく、実機通信とWindows EXEの動作は未検証です。
実装・検証結果と実機試験の手順は [STATUS.md](STATUS.md) を参照してください。

## 指定フォルダーでWindowsビルド・起動

GitHubの最新mainを取得し、**フォルダー全体**を次の場所に展開してください。
すでに展開済みの場合は、追加された `scripts/build_windows.ps1` がある最新の内容に更新してください。

```text
C:\Users\ATSUYA_WATANABE\Documents\robstride-rs02-configurator-main
```

Python 3.11 **64ビット版**とPythonランチャーが必要です。未導入の場合はPowerShellで：

```powershell
winget install --exact --id Python.Python.3.11
```

インストール後にPowerShellを開き直し、次を実行します。

```powershell
Set-Location 'C:\Users\ATSUYA_WATANABE\Documents\robstride-rs02-configurator-main'
Unblock-File '.\scripts\build_windows.ps1'
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File '.\scripts\build_windows.ps1' -Launch
```

`RemoteSigned` はこのPowerShellプロセスだけに適用し、PCの恒久的な実行ポリシーは変更しません。
スクリプトはPython 3.11 x64を確認し、ローカルの `.build-tools` にuvを導入して、
`uv.lock` に従って依存関係を準備します。DLL読込み・テスト・lint・型チェックが成功した後に
PyInstallerでEXEを作成し、同梱libusb DLLの存在を確認します。`-Launch` を付けると起動します。
インターネット接続が必要です。既存の `dist\RS02Configurator` はビルド結果で更新されます。
Windows固有のこのスクリプトは、クラウドでWindowsビルドを実行した証拠にはなりません。

成果物：

```text
dist\RS02Configurator\RS02Configurator.exe
dist\RS02Configurator-Windows-x64-ja.zip
```

配布する場合はEXE単体でなく、`_internal` とライセンスを含む **RS02Configuratorフォルダー全体**
またはZIPを渡してください。署名付きインストーラーではありません。

## USB2CAN-X2で使う

先に [Windows／Zadigの手順](docs/windows_setup.md) に従って、確認したX2のUSBインターフェースを
**libusbK** に割り当ててください。このドライバー導入はビルドスクリプトでは行いません。
Pythonと同じ64ビットのlibusb DLLは `libusb-package` から同梱します。
System32へのDLLコピーは不要です。

1. モーターを固定し、軸の周囲を空け、物理的に電源を切れる状態にします。
2. 電源を切ってCAN_H／CAN_L・基準線・終端・電源極性を確認します。まず1台だけ接続します。
3. モーターがRobStrideの私有プロトコル、拡張ID、1 Mbit/sに設定されていることを確認します。
4. アプリで「実機：USB2CAN-X2 / gs_usb」→「USBを検索」→X2を選択します。
   デバイス名・シリアル・ラベルを照合してください。対応VID/PIDだけでX2とは断定できません。
5. 接続したCAN 0／CAN 1と **1,000,000 bit/s** を選択し、「接続」→「モーターを検索」を実行します。
   応答したモーターには速度ゼロ・無効化を要求します。ID 1～254を検索し、255はホスト用に予約します。
6. UUID・状態・異常・測定値を確認します。「設定を適用・読み戻し確認」でパラメーターを確認します。
7. 必要なときだけ「モーターを有効化」を押し、確認ダイアログで「実行する」を選びます。
   正転・逆転ジョグは速度モード (2) で、ボタンを押している間だけ動作します。

初期値は **電流0.5 A・速度0.5 rad/s・加速度0.5 rad/s²・ジョグ0.2 rad/s** です。
MVPでは電流・速度・加速度を各単位で最大2、ジョグを最大0.5 rad/sに制限します。
これらは控えめなアプリ設定であり、実機の安全動作を保証する値ではありません。

CAN ID変更には明示的な確認が必要です。**他のモーターを物理的に取り外し、必ず1台だけにしてください。**
変更先IDの応答を確認し、変更後に同じUUIDであることを照合します。
結果が不確実な場合は通信を閉じ、新旧のIDで停止・無効化を試みます。

## 設定・ログ・停止

- 位置はrad、速度はrad/s、電流はフィルター済みIqのA、温度は°Cです。
  電流をトルクから推測しません。温度・状態は最後に受信した状態フレームの値です。
- JSONは希望する設定値だけを保存します。接続状態・有効化状態は保存しません。
  読み込みは切断中だけ可能で、読み込みによる接続・モーター書込みは行いません。
- CSVは直近10,000件のCAN記録を出力し、GUIは直近2,000行を表示します。
  ターミナルから起動した場合は構造化JSONログも標準エラーに出力します。
- ジョグ解放で速度ゼロを要求します。「停止」、フォーカス喪失、350 msの継続通知途絶では無効化も試みます。
  停止・切断・終了が構成設定中に要求された場合、後続の有効化を取り消します。
- 切断・終了・CANワーカーの例外では **速度ゼロ要求 → 無効化の試行 → 通信を閉じる** の順に処理します。
  個々の失敗後も後続処理を試み、失敗をログに表示します。

USB抜去・PC停止・プロセス強制終了・電源喪失では指令の配送と停止を保証できません。
停止対象は発見済みのIDです。架空の一斉停止フレームは送信しません。
ファームウェアのCANウォッチドッグ・パラメーター永続化は別途確認が必要です。
このMVPは推測によるウォッチドッグ設定、フラッシュ保存、原点設定、校正を行いません。
ライブラリー由来の詳細エラー・識別子は調査用に元の英語表記が残る場合があります。

## 開発・手動起動

uv導入済みの場合、リポジトリ直下で：

```sh
uv sync --frozen --extra dev --python 3.11
uv run --frozen rs02-configurator
```

uvを使わないWindowsの手動インストール（完全なロック再現にはuvを使用）：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\rs02-configurator.exe
```

Linux／クラウドの検証：

```sh
QT_QPA_PLATFORM=offscreen uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check src tests hardware_smoke_test.py desktop_main.py
uv run --frozen --extra dev mypy
uv run --frozen python hardware_smoke_test.py --backend simulated --csv /tmp/rs02-smoke.csv
```

WindowsのEXEはWindows上でビルドしてください。LinuxからWindows EXEを作ったとは主張しません。

## 構成・公式SDK

`src/gui`：日本語GUI、`src/service`：設定・安全制御・ログ・ワーカー、
`src/transport`：gs_usb／SocketCAN／シミュレーション、`src/protocol`：公式SDKのアダプター、
`tests`：サービス・USBモック・Qt GUIの自動テストです。

公式SDKのPythonファイルは変更せず、MITライセンスとコミット・ハッシュを保持しています。
[出典と修正根拠](vendor/PROVENANCE.md) を参照してください。
SDKのSocketCAN固定を通信層に分離し、受信の照合・タイムアウトと公式C++に基づくID変更を補っています。
CAN0／CAN1はメーカー公開gs_usbドライバーに基づいて選択します。実機の番号対応は未検証です。

Linuxで物理CANを使う場合は、OS側でSocketCANの速度を設定してください。
GUIからSocketCANのビットレートは変更できません。同じUSBデバイスを別のCANアプリと共有しないでください。

```sh
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
ip -details link show can0
```
