# Hakoniwa Workspace Environment

[English](hakoniwa-workspace-environment.md)

## 目的

Hakoniwa Foundationは、共通バイナリ、ライブラリ、Python venv、Core設定、runtime状態を`work/foundation/`へ集約します。

Hakoniwa Workspace Environmentは、その物理的な配置境界に対応する**プロセス環境の境界**です。DockerのようにOS全体を隔離するものではありません。箱庭を実行するシェルまたは子プロセスだけに、Foundation workspaceを優先する環境変数を適用します。

これにより、ユーザー環境に残っている古い`hakopy.pyd`、永続的な`PYTHONPATH`、別venv、system Pythonなどが、現在のFoundationより先に選択されることを防ぎます。

## 管理対象

標準workspaceは次です。

```text
work/foundation/
├── install/
│   ├── bin/
│   ├── lib/
│   ├── python/                  # Foundation共通venv
│   └── share/hakoniwa/python/  # Core hakopy
├── config/cpp_core_config.json
├── activate
└── Activate.ps1
```

Workspace Environmentは次を設定します。

- `HAKONIWA_WORKSPACE_ACTIVE=1`
- `HAKONIWA_WORKSPACE_ROOT=<business-pack-root>`
- `HAKONIWA_HOME=<workspace>/work/foundation/install`
- `HAKO_CONFIG_PATH=<workspace>/work/foundation/config/cpp_core_config.json`
- `VIRTUAL_ENV=<workspace>/work/foundation/install/python`
- `PYTHONNOUSERSITE=1`
- Foundation PythonとFoundation `bin`を`PATH`の先頭へ追加
- LinuxではFoundation `lib`を`LD_LIBRARY_PATH`の先頭へ追加
- macOSではFoundation `lib`を`DYLD_LIBRARY_PATH`の先頭へ追加
- WindowsではFoundation `bin`をDLL探索用の`PATH`先頭へ追加

次は引き継ぎません。

- `PYTHONPATH`
- `PYTHONHOME`

Foundation venvと、そこへ導入された`.pth`およびpackageがPython module選択を所有します。

## 準備

Foundation workspace用のactivation scriptを生成します。

```bash
python tools/workspace.py prepare
```

生成物は`work/`配下のローカル成果物であり、Git管理しません。

## 入る・出る

### 子シェルを使う

もっとも安全な入口です。

```bash
python tools/workspace.py enter
```

隔離された子シェルが開きます。`exit`すると元のシェルへ戻ります。親シェルの環境変数は変更されません。

### 現在のPOSIXシェルへ適用する

```bash
source work/foundation/activate
```

終了時は次を実行します。

```bash
deactivate_hakoniwa
```

activation前に設定されていた環境変数は、未設定と空文字を区別して復元します。

### PowerShellへ適用する

```powershell
. .\work\foundation\Activate.ps1
```

終了時は次を実行します。

```powershell
Exit-HakoniwaWorkspace
```

## 1コマンドだけ実行する

CI、Recipe wrapper、AI agent、非対話実行では、activation済みシェルを前提にしません。

```bash
python tools/workspace.py run -- <command> [args...]
```

例:

```bash
python tools/workspace.py run -- python tools/foundation.py doctor \
  --recipe recipes/examples/drone-single-mujoco-threejs.yaml
```

`enter`、activation script、`run`は同じ環境契約を使用します。

## Python bindingの検証

次で、実際に選択されたPythonとmodule originを確認します。

```bash
python tools/workspace.py doctor
```

次を検証します。

- `sys.executable`がFoundation venv配下である
- `sys.prefix`がFoundation venv配下である
- `hakopy`がFoundation workspace配下から解決される
- `hakoniwa_pdu`がFoundation workspace配下から解決される
- `hakoniwa_pdu_endpoint`がFoundation workspace配下から解決される

外部`PYTHONPATH`に古い`hakopy`があっても、それを許容して警告だけ出すのではなく、Foundation境界違反として失敗させます。

## 責務境界

| レイヤ | 責務 |
| --- | --- |
| Component `hako.py install` | Python/native artifactをFoundation install境界へ配置する |
| Foundation workspace | 共通venv、Core Python、config、runtime、Receiptを所有する |
| Workspace Environment | ambient Python/path状態を遮断し、Foundationをプロセス環境の先頭へ置く |
| `doctor` / smoke | 実際に解決されたPython/module originを証拠として確認する |
| Recipe / Launcher wrapper | `workspace.py run`または同じ環境契約でプロセスを起動する |

## 非スコープ

- OS filesystem、network、user namespaceの隔離
- dependencyをコンテナimageへ固定すること
- 複数Foundation profileの同時切り替え
- system Pythonやユーザーの既存venvの変更・削除

必要なのはコンテナではなく、現在選択したHakoniwa Foundationを明示的に所有する、再現可能な実行環境です。
