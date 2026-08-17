# はじめての hakoniwa-business-pack（導入ガイド）

このガイドは、箱庭（Hakoniwa）をまだ知らない人が
「このリポジトリで何ができて、どう使い始めればよいか」を掴むためのものです。

---

## 1. これは何？

**箱庭でやりたいことを、AI と一緒に「動く形」にするためのガイドブック＋工具箱** です。

箱庭は、ロボットやドローンをコンピュータ内で動かして試すシミュレーション基盤で、
物理エンジン（MuJoCo）、3D 表示（Godot / Three.js）、通信（PDU）、
複数プログラムの時間を揃える仕組み（Conductor）など、多くの部品でできています。

部品が多いぶん「どれをどう組み合わせるか」が初心者には難しい。
このリポジトリはその悩みを、次の 4 つで解決します。

| 名前 | 役割 | 場所 |
|---|---|---|
| **Catalog（部品図鑑）** | 各部品の「できること・必要なもの・制約」を整理 | `catalog/` |
| **Recipe（組み立てレシピ）** | 部品の組み合わせ手順と検証状態を記述した設計書 | `recipes/examples/` |
| **Use Case（やりたいこと集）** | ユーザー要望の蓄積。「今できる／まだできない」の記録 | `usecases/` |
| **Tools（実行ツール）** | Recipe を読んで、取得・ビルド・環境準備・起動を自動化 | `tools/` |

コードを書くリポジトリというより、**AI に読ませて相談相手にする** のが本来の使い方です。

---

## 2. 使い方の全体像

```text
「箱庭でこんなことがしたい」
        |
        v
  AI が Catalog / Recipe / Use Case を読む
        |
        v
  「この部品の組み合わせで実現できる / ここは未検証」と答える
        |
        v
  既存 Recipe があれば configure -> launch で動くデモを作る
        |
        v
  結果や課題を Catalog / Recipe / Use Case に還元
```

---

## 3. AI に読ませる（最初にやること）

Claude Code などの AI エージェントをこのリポジトリのルートで起動し、
まず次のように頼みます。

```text
README.md の「AI Bootstrap Prompt」に従ってリポジトリを読み込んで
```

AI は `AGENTS.md` → エコシステム解説 → Catalog → Runtime Primer → Recipe の順に読み、
「何を理解したか」を報告します。その後で自由に質問できます。

質問の例:

- 「TurtleBot3 を MuJoCo で走らせて、Godot で見たい。できる？」
- 「ドローンをゲームパッドで操作するデモはある？」
- 「ROS 2 のサービスを箱庭とつなぐレシピを教えて」

AI は Recipe があれば示し、無ければ「Recipe 案」と実現可能性を答えます。
**ローカルでビルドや起動をするのは、こちらから明示的に頼んだときだけ** です。

---

## 4. 環境構築

デモを動かすには、まず OS ごとに前提ツールを入れ、次にリポジトリを取得します。

### 4.1 必要なもの

| ツール | 用途 | 備考 |
|---|---|---|
| Git | このリポジトリと部品リポジトリの clone | |
| Python **3.12** | Foundation（共通基盤）の必須バージョン | 3.11 / 3.13 では動きません（native 拡張の ABI を 3.12 に固定） |
| Ruby 3.x | `recipe.py` が Recipe YAML の読み込みに使用 | 追加 gem は不要（同梱の psych / json のみ） |
| CMake 3.16+ | C++ 部品のビルド | |
| C++ コンパイラ | 同上 | Windows: Visual Studio 2022（C++ デスクトップ開発）/ macOS: Xcode CLT / Linux: gcc |
| Boost（ヘッダのみ） | `hakoniwa-pdu-endpoint` の WebSocket 実装（Boost.Asio / Boost.Beast） | Windows は vcpkg、macOS は Homebrew、Linux は apt |
| GLFW / OpenGL | MuJoCo ビューア・センサ描画（MuJoCo 系 Recipe） | Windows は vcpkg、macOS は Homebrew、Linux は apt |

> Python 3.12 と Ruby は「あれば良い」ではなく、無いと `recipe.py doctor` すら動きません。

### 4.2 Windows 11（x64）

PowerShell で実行します。管理者権限は不要です。

```powershell
# Python 3.12 / Ruby 3.3 / CMake / Git（未導入のものだけ）
winget install --id Python.Python.3.12 -e
winget install --id RubyInstallerTeam.Ruby.3.3 -e
winget install --id Kitware.CMake -e
winget install --id Git.Git -e
```

Visual Studio 2022 は Visual Studio Installer で
「C++ によるデスクトップ開発」ワークロードを入れてください
（Build Tools 版でも可）。

Boost は **自分で clone した vcpkg** から入れます。VS 同梱の vcpkg
（`C:\Program Files\...\VC\vcpkg`）は Program Files 配下でポート定義も
持たないため使えません。

```powershell
git clone https://github.com/microsoft/vcpkg.git D:\vcpkg
D:\vcpkg\bootstrap-vcpkg.bat -disableMetrics
D:\vcpkg\vcpkg.exe install boost-asio:x64-windows boost-beast:x64-windows glfw3:x64-windows
```

Boost の依存（regex, date-time など）をソースからビルドするため
10〜20 分ほどかかります。`glfw3` は MuJoCo 系 Recipe（TurtleBot3 など）の
シミュレータビルドに必要です。

インストール後、**新しい PowerShell を開き直して** PATH を反映し、確認します。

```powershell
py -3.12 --version     # Python 3.12.x
ruby --version         # ruby 3.3.x
cmake --version
```

### 4.3 macOS（Apple Silicon / Intel）

```bash
xcode-select --install                 # Xcode Command Line Tools（未導入なら）
brew install python@3.12 ruby cmake boost glfw
```

Homebrew の Boost を CMake から見えるようにします（シェルの rc に入れておくと便利）。

```bash
export CMAKE_PREFIX_PATH="$(brew --prefix boost)${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
```

確認:

```bash
python3.12 --version
ruby --version
cmake --version
```

`tools/doctor-mac.bash` を実行すると、macOS 向けの前提チェックをまとめて行えます。

### 4.4 Linux（Ubuntu 22.04 / 24.04）

```bash
sudo apt-get update
sudo apt-get install -y git build-essential cmake ruby-full \
  libboost-dev libglfw3-dev libopengl-dev
```

Ubuntu 22.04 の標準 Python は 3.10 なので、3.12 を別途入れます。

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
python3.12 --version
```

（Ubuntu 24.04 は `apt-get install python3.12 python3.12-venv` で入ります。
pyenv などで 3.12 を用意しても構いません。）

### 4.5 リポジトリの取得

部品リポジトリはこのリポジトリの **隣** に clone されるので、
専用の親ディレクトリを作ってその中に置きます。

```bash
mkdir -p ~/Hakoniwa && cd ~/Hakoniwa          # Windows 例: D:\Hakoniwa
git clone https://github.com/hakoniwalab/hakoniwa-business-pack.git
cd hakoniwa-business-pack
```

Foundation 部品（core-pro / pdu-python / pdu-endpoint）は `recipe.py configure` が
自動で clone しますが、**Recipe 固有の部品は自動 clone されません**。
`mujoco-turtlebot3-wall-follower` では次の 2 つを手動で隣に置きます。

```bash
cd ~/Hakoniwa                                   # 親ディレクトリで
git clone --recursive https://github.com/hakoniwalab/hakoniwa-mbody-registry.git
git clone --recursive https://github.com/hakoniwalab/hakoniwa-mujoco-robots.git
```

（どの Recipe が何を要求するかは、Recipe YAML の `recipe_local_requirements` と
`catalog/components/<id>.yaml` の `url` で確認できます。）

最終的な配置イメージ:

```text
Hakoniwa/
├── hakoniwa-business-pack/     # このリポジトリ
├── hakoniwa-core-pro/          # configure が自動 clone
├── hakoniwa-pdu-python/        # 同上
├── hakoniwa-pdu-endpoint/      # 同上
├── hakoniwa-mbody-registry/    # 手動 clone（Recipe 固有）
└── hakoniwa-mujoco-robots/     # 手動 clone（Recipe 固有）
```

### 4.6 Windows のみ: vcpkg を Foundation に登録

`recipe.py configure` を実行する **前** に、4.2 で作った vcpkg の場所を
Foundation の toolchain 設定として保存します（親シェルの `VCPKG_ROOT` は変更しません）。

```powershell
py -3.12 tools\foundation.py toolchain --recipe-id mujoco-turtlebot3-wall-follower --vcpkg-root D:\vcpkg
```

`work\foundation\config\toolchain.json` に書き込まれ、以後の Endpoint ビルドが参照します。

---

## 5. デモを動かす

### 5.1 Recipe を選ぶ

`recipes/examples/` から 1 つ選びます。最初におすすめなのは、
macOS arm64 と Windows x64 で実行検証済みの **`mujoco-turtlebot3-wall-follower`**
（TurtleBot3 が LiDAR で壁沿いに走る）です。Linux も対応対象ですが、現時点では
この Recipe の end-to-end 実行検証は記録されていません。

### 5.2 手順（OS 共通）

リポジトリのルートで実行します。

```bash
# 1. 隔離された作業シェルに入る（プロンプト先頭に (hako) が付く）
python3.12 tools/workspace.py enter      # Windows: py -3.12 tools\workspace.py enter

# 2. 何が足りないか確認（ビルドはしない）
python tools/recipe.py doctor --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml

# 3. これから何を clone / build するか確認
python tools/recipe.py plan   --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml

# 4. Foundation を構築（clone -> build -> install -> Python 依存導入）  ※初回は 10 分前後
python tools/recipe.py configure --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml

# 5. 使い方ガイド（HTML）を生成してブラウザで開く
python tools/recipe.py guide  --recipe recipes/examples/mujoco-turtlebot3-wall-follower.yaml --open
```

`(hako)` シェルの中では `python` が Foundation venv の Python 3.12 を指すので、
手順 2 以降は `python` で構いません。

手順 4 の最後に次のように出れば Foundation は完成です。

```text
Foundation: SATISFIED
[SATISFIED] Foundation Python: 3.12.x ...
[SATISFIED] hakoniwa-core-pro
[SATISFIED] hakoniwa-pdu-python
[SATISFIED] hakoniwa-pdu-endpoint
```

続けて `python tools/workspace.py doctor` を実行し、
`[OK] Foundation Python and Hakoniwa modules are workspace-owned.` と出れば OK です。

`configure` は **既存の checkout を勝手に更新・削除しません**。
2 回目以降は差分だけがビルドされます。

### 5.3 デモを起動する（Recipe 専用ランナー）

Recipe によって起動方法は 2 通りあります。

- Recipe YAML に `runtime.launcher` があるもの → `python tools/recipe.py launch --recipe ...`
- Recipe 専用ランナー（`tools/recipe/<recipe>.py`）があるもの → そのランナーを使う

`mujoco-turtlebot3-wall-follower` は後者です。Recipe YAML の `runbook` に書かれた順で実行します
（`(hako)` シェルの中で）。

```bash
# 1. MBody と controller 入力を配置し、Recipe 用 CMake を構成
python tools/recipe/mujoco_turtlebot3_wall_follower.py configure

# 2. TB3 シミュレータをビルド（初回のみ数分）
python tools/recipe/mujoco_turtlebot3_wall_follower.py build

# 3. 準備確認（"status": "READY" なら OK）
python tools/recipe/mujoco_turtlebot3_wall_follower.py doctor

# 4. 起動（MuJoCo ビューアと LiDAR プロットが開き、20 秒後に自動終了）
python tools/recipe/mujoco_turtlebot3_wall_follower.py start

# 5. 状態確認 / 途中で止めたいとき
python tools/recipe/mujoco_turtlebot3_wall_follower.py status
python tools/recipe/mujoco_turtlebot3_wall_follower.py stop

# 6. 終わったら作業シェルを抜ける
exit
```

`start` はバックグラウンドで Launcher を起動し、
`work/recipes/mujoco-turtlebot3-wall-follower/runtime/launcher-session.json` の
`state` が `RUNNING` → `TERMINATED` と遷移すれば正常終了です。
ログは `work/recipes/mujoco-turtlebot3-wall-follower/logs/` にあり、
`obstacle_avoider.out` に `mode=FOLLOWING`、`tb3_sim_mbody.out` に
`lidar_hits=` が非ゼロで出ていれば成功です。

オプション: `--headless`（ビューア無し）、`--duration-sec 60`（実行時間）、
`--model burger`（機体変更）。

### 5.4 どこに何ができるか

```text
work/                         # git 管理外。消しても再構築できる
├── foundation/
│   ├── install/              # 共通バイナリ・ライブラリ・Python venv
│   ├── config/               # Core 設定・toolchain 設定
│   └── build/                # ビルド作業領域
└── recipes/<recipe-id>/      # Recipe 固有の設定・ログ・実行状態
```

複数の Recipe を試しても Foundation は共有され、差分だけがビルドされます。

---

## 6. 困ったとき

| 症状 | 見るところ |
|---|---|
| `error: [WinError 2]` / `ruby: command not found` | Ruby 未導入。インストールして PATH を通す |
| `Foundation Python 3.12 ...` のエラー | Python 3.12 で `workspace.py` を起動しているか確認 |
| `Boost headers were not discovered` | Boost 未導入。表示される `vcpkg install ...` / brew / apt を実行 |
| `[WARNING] Hakoniwa Workspace is not active` | `workspace.py enter` の外で実行している。enter するか `workspace.py run -- <cmd>` を使う |
| Python モジュールが別 venv から読まれる | `python tools/workspace.py doctor` で `sys.executable` と module origin を確認 |

---

## 7. 次に読むもの

- `README.md` — 全体像と AI Bootstrap Prompt
- `docs/hakoniwa-base-ecosystem-ja.md` — 箱庭の共通基盤（PDU / Endpoint / Conductor など）
- `docs/hakoniwa-component-asset-guide-ja.md` — 主要部品の位置付け
- `docs/hakoniwa-workspace-environment-ja.md` — `(hako)` 作業シェルの仕組み
- `foundation/README.md` — Foundation（共通基盤）の設計
- `recipes/README.md` — Recipe の書き方と考え方
- `docs/hakoniwa-agent-human-boundary.md` — AI が実行してよい範囲と人が判断する範囲