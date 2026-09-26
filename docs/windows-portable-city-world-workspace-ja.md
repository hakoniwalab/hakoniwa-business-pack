# City World Windows Portable Workspace

## 1. 目的

City World Web UI を、Windows の一般ユーザーが Business Pack の開発環境を
構築せずに利用できる配布形態へ変換する。

配布物は、Windows x64 上で構築・検証済みの
`hakoniwa-business-pack` Workspace と必要な sibling repository、
portable Python runtime を一つの ZIP にまとめる。

利用者の操作は次だけでよい。

```text
ZIPを取得
  ↓
任意のフォルダへ展開
  ↓
start-city-world.bat
  ↓
ブラウザでCity World Web UIを操作
```

利用側では Python、Git、WSL、Docker の追加インストールを要求しない。

## 2. 配布物の構成

生成される ZIP は次の構成を持つ。

```text
hakoniwa-business-pack-city-world-windows-x64/
├── start-city-world.bat
├── status-city-world.bat
├── stop-city-world.bat
├── README-WINDOWS.txt
├── portable-package.json
├── hakoniwa-business-pack/
│   ├── tools/
│   └── work/
│       ├── foundation/
│       │   └── install/
│       │       ├── bin/
│       │       ├── lib/
│       │       └── python/
│       │           └── python.exe
│       └── recipes/
│           └── city-world-web-ui/
│               ├── environment.json
│               └── config/launcher.json
├── hakoniwa-envsim/
├── hakoniwa-pdu-javascript/
└── hakoniwa-pdu-python/
```

`hakoniwa-envsim`、`hakoniwa-pdu-javascript`、`hakoniwa-pdu-python` は
revisionを追跡するsource依存として sibling
layout を維持するため、City World の既存 path 解決を変更しない。

## 3. なぜ venv をそのまま ZIP にしないか

Python の通常の venv は、作成元 Python の配置を `pyvenv.cfg` などに保持するため、
別PC・別展開先へそのまま移動する配布形式にはしない。

package tool は Windows 公式 embeddable Python を基盤にし、構築済み Workspace から
必要な `site-packages` を移植する。

portable Python runtime はFoundationに一つだけ配置する。

- Foundation runtime:
  `work/foundation/install/python/python.exe`

通常の開発 Workspace は従来どおり
`Scripts/python.exe` を使用する。
`tools/workspace.py` と `city-world-web-ui` Recipe は、
portable runtime が存在する場合だけ root の `python.exe` を優先する。

## 4. package の作成条件

package は **Windows x64 上で作成する**。

クロスビルドは行わない。Foundation の native binary と Python wheel を
実際の配布対象OS/architectureで構築・検証してから package 化する。

前提:

- Windows x64
- CPython 3.12 の Foundation Workspace が構築済み
- `../hakoniwa-envsim` が存在する
- `../hakoniwa-pdu-javascript` が存在する
- `../hakoniwa-pdu-python` が存在する
- source Workspace の `python tools/recipe/city_world_web_ui.py doctor` が成功する
- Python package の取得と PLATEAU API 利用に必要なネットワーク接続がある

## 5. package の作成

Business Pack root で実行する。

```powershell
python tools/package_portable_workspace.py
```

既定の出力先:

```text
dist/hakoniwa-business-pack-city-world-windows-x64.zip
```

package tool は次を行う。

1. `city-world-web-ui` RecipeのCore-free Endpoint、Python requirements、runtime設定の構成とdoctor
2. source / stagingの双方でCore-free Recipe doctorを実行（`hakopy`と`hakoniwa_pdu`は要求しない）
3. Python runtime のversion/architecture確認
4. 同じversionの公式 Windows embeddable Python の取得
5. Foundationの `site-packages` 移植
6. Business Pack / Envsim / PDU JavaScript / PDU Python の source 配置
7. 過去の City World job / PLATEAU cache を除外
8. staging内でportable runtime用のWorkspace activationを準備
9. portable `city-world-web-ui` RecipeのdoctorとCore-free import確認
10. ZIP生成と SHA-256 表示

既に公式 embeddable ZIP を取得済みなら、ネットワーク取得を避けられる。

```powershell
python tools/package_portable_workspace.py ^
  --python-embed-zip C:\downloads\python-3.12.x-embed-amd64.zip
```

staging の中身を確認したい場合:

```powershell
python tools/package_portable_workspace.py --keep-staging
```

`work/portable-package/` に展開済み package を残す。

## 6. 利用者の操作

1. ZIPを任意のフォルダへ展開する。
2. `start-city-world.bat` をダブルクリックする。
3. ブラウザが開いたら City World Web UI を操作する。
4. 必要に応じて `status-city-world.bat` で状態確認する。
5. 終了時は `stop-city-world.bat` を実行する。

起動バッチは packaged Foundation Python を使って、

```text
workspace.py run
  -> tools/recipe/city_world_web_ui.py start
  -> Worker + Web server
  -> browser open
```

を実行する。

## 7. 展開先の制約

package は展開先の絶対パスを固定しない。

例えば、次のどちらでも同じ package を利用できる。

```text
C:\Users\alice\Downloads\hakoniwa-city-world\
D:\tools\hakoniwa-city-world\
```

Workspace environment と City World Launcher は実行時に
Business Pack root から path を再解決する。

package tool は `.pth` 内に source PC の絶対パスが残っている場合、
portable package の生成を停止する。
Workspace bootstrap の `.pth` は利用時の展開先に合わせて
`workspace.py prepare` が再生成する。

## 8. 保存データ

利用者が生成した City World job と PLATEAU共有cacheは package 内の

```text
hakoniwa-business-pack/work/recipes/city-world-web-ui/runtime/
```

に保存される。旧 `work/remote-operation/` は新しいpackageでは使用しない。

```text
hakoniwa-business-pack/work/remote-operation/
```

以下へ保存する。

配布用ZIPには、package作成PCで過去に生成したjobやPLATEAU cacheを含めない。

## 9. 現時点の対象

最初の portable package は City World Web UI を対象とする。

ただし設計上は、

```text
Recipe
  ↓
configured Workspace
  ↓
portable package
  ↓
user launcher
```

という Business Pack の配布モデルとして再利用できる。

今後、他の Recipe でも同じ方式を採用する場合は、
Recipeごとの runtime dependency と entrypoint を宣言して
general-purpose package command へ拡張する。

## 10. リポジトリが持つprofile（repository profile）

City World と Urban Car の profile は `tools/portable_package_profiles.py` に組み込まれている。
それ以外のアプリは、Business Pack を変更せずに、自分のリポジトリへ profile を置ける。

```text
<sibling repository>/portable/windows-profile.json
```

package tool は、Business Pack の親ディレクトリにある sibling repository から
このファイルを探し、`--profile <id>` で選べるようにする。

```powershell
python tools/package_portable_workspace.py --profile fpv-drone-master3x
```

profile は次を宣言する（`schema_version: 1`）。

| key | 内容 |
|---|---|
| `id` / `package_id` / `recipe_id` / `title` | profile ID、ZIP名、元の Recipe、表示名 |
| `repositories` | 同梱する sibling repository。`name`、`required_artifact`、`include_paths`。所有リポジトリ自身を含める |
| `python_paths` | portable Python の `._pth` に加える、package root からの相対パス |
| `tool` | 所有リポジトリからの相対パスで書いた portable tool |
| `entrypoint_name` | `start-<name>.bat` / `status-<name>.bat` / `stop-<name>.bat` の名前 |
| `readme` | ZIP root の `README-WINDOWS.txt` になるファイル |
| `validation_imports` | staging で import を確認する Python module |
| `staging_cleanup` | staging の検証で生成され、ZIPに入れないパス（package root からの相対パス） |

パスはすべて package 内の相対 POSIX パスで書き、絶対パスや `..` は拒否する。
Python 依存は Recipe configure が Foundation Python へ入れたものを移植し、
package 作成時にネットワークから追加しない。

portable tool は次のコマンドを実装する。

| コマンド | 実行場所 | 役割 |
|---|---|---|
| `collect` | 作成元 Workspace | 作成元にしかない実行時ファイル（vcpkg の DLL など）を、同梱するパスへ集める |
| `doctor` | 作成元 Workspace | 同梱する入力がそろっているか確認し、足りなければ非0で終わる |
| `prepare` | package（staging 検証と初回 start） | `core_mmap_path` などを展開先に合わせ、アプリの runtime を展開先向けに構成する |
| `start` / `status` / `stop` | package | 起動バッチから呼ばれる。`start` は自分で `prepare` を行う |

起動バッチは、同梱した Foundation と Workspace を選ぶ環境変数
（`HAKONIWA_PORTABLE_WORKSPACE=1`、`HAKONIWA_HOME`、`HAKO_CONFIG_PATH`、`VIRTUAL_ENV` など）を設定し、
既存の `PYTHONPATH` / `PYTHONHOME` を消してから、ポータブル Python で portable tool を実行する。
ZIP 化の前に、`staging_cleanup` のパスと `work/foundation/runtime/mmap` を削除し、
`core_mmap_path` を `__HAKONIWA_PORTABLE_MMAP__` に置き換える。

例: `hakoniwa-fpv-drone/portable/windows-profile.json` と `hakoniwa-fpv-drone/tools/fpv_portable.py`。
