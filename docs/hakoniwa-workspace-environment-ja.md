# Hakoniwa Workspace Environment

[English](hakoniwa-workspace-environment.md)

## 目的

Hakoniwa Foundationは、共通バイナリ、ライブラリ、Python venv、Core設定、runtime状態を`work/foundation/`へ集約します。

Hakoniwa Workspace Environmentは、そのFoundationを選択した**子プロセスだけ**へ適用する実行環境です。ユーザーの親シェルやsystem Pythonを書き換えず、古い`hakopy.pyd`、永続的な`PYTHONPATH`、別venvなどが現在のFoundationより先に選択されることを防ぎます。

## 標準ユーザー操作

OSに関係なく、入口は次の1コマンドです。

```bash
python tools/workspace.py enter
```

`enter`は内部で必要な準備を更新してから、隔離された子シェルを起動します。

- activation scriptを再生成する
- Foundation venvがある場合はPython bootstrap `.pth`を更新する
- 最新のWorkspace環境変数を構築する
- ユーザーprofileを読み込まない子シェルを起動する
- プロンプト先頭へ`(hako)`を表示する

例:

```text
(hako) tmori@TakashinoMBP hakoniwa-business-pack %
```

この表示がある間だけHakoniwa Workspace Environment内です。

作業を終了するときは、OSに関係なく次を実行します。

```bash
exit
```

子シェルが終了し、親シェルへ戻ります。親シェルの環境変数は変更されていないため、個別の復元操作は不要です。

標準運用は次の対です。

```text
enter -> Hakoniwa作業 -> exit
```

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
- `HAKO_PDU_ENDPOINT_RUNTIME_DIRS=<workspace>/work/foundation/install/bin`
- `VIRTUAL_ENV=<workspace>/work/foundation/install/python`
- `PYTHONNOUSERSITE=1`
- Foundation Python、binary、library path

`PYTHONPATH`と`PYTHONHOME`は引き継ぎません。

## 1コマンドだけ実行する

CI、Recipe wrapper、AI agentなどの非対話実行では、次を使います。

```bash
python tools/workspace.py run -- <command> [args...]
```

`run`も内部で`prepare`相当を実行してから、同じWorkspace環境でコマンドを起動します。

例:

```bash
python tools/workspace.py run -- python tools/foundation.py doctor \
  --recipe recipes/examples/drone-single-mujoco-threejs.yaml
```

## Python bindingの検証

Workspaceへ入った後、次で実際に選択されたPythonとmodule originを確認できます。

```bash
python tools/workspace.py doctor
```

次を検証します。

- `sys.executable`がFoundation venv配下である
- `sys.prefix`がFoundation venv配下である
- `hakopy`、`hakoniwa_pdu`、`hakoniwa_pdu_endpoint`がFoundation workspace配下から解決される

Business Pack FoundationのPython契約はCPython 3.12です。
`tools/foundation.py`はcomponentのbuild/install前にこのidentityを検証し、Coreには
SOABI付き`hakopy`を要求します。Foundation `doctor`は、現在のinterpreterのSOABIと
Core Component Receiptに記録されたPython metadataを比較します。古いReceiptに
metadataがない場合やABIが一致しない場合は`INCOMPATIBLE`とし、untagged extensionへ
暗黙fallbackしません。

## 互換用の低レベル操作

`prepare`、POSIXの`activate`、PowerShellの`Activate.ps1`は互換性とデバッグのために残します。ただし、通常ユーザー向けの標準操作ではありません。

```bash
python tools/workspace.py prepare
source work/foundation/activate
deactivate_hakoniwa
```

```powershell
. .\work\foundation\Activate.ps1
Exit-HakoniwaWorkspace
```

新しい運用では、OS差をユーザーへ露出しない`enter`と`exit`を使用してください。

## 責務境界

| レイヤ | 責務 |
| --- | --- |
| Component `hako.py install` | Python/native artifactをFoundation install境界へ配置する |
| Foundation workspace | 共通venv、Core Python、config、runtime、Receiptを所有する |
| Workspace Environment | ambient Python/path状態を遮断し、Foundationを子プロセス環境の先頭へ置く |
| `doctor` / smoke | 実際に解決されたPython/module originを確認する |
| Recipe / Launcher wrapper | `workspace.py run`または同じ環境契約でプロセスを起動する |

必要なのはOSごとのactivation手順ではなく、選択したHakoniwa Foundationへ入って、作業後にその子シェルを終了する明確な運用です。

## Recipe 境界を迂回しない

Foundation の `doctor`、`plan`、`build` が、選択した Recipe の
`foundation_requirements` 欠落や不正を報告した場合、その結果は Recipe の妥当性を
示す停止条件です。兄弟 Component repository の `tools/hako.py`、doctor、build、
install を直接実行して処理を続けてはいけません。

Component repository は Recipe と Foundation が利用する source input です。
Component-local な `.hako`、venv、build、install は、Recipe がその path の所有を
明示していない限り、Foundation の状態や検証証跡にはなりません。

管理対象の実行可能 Recipe は、次を宣言します。

```yaml
execution_environment:
  workspace:
    mode: managed

foundation_requirements:
  component-id:
    capabilities:
      required_capability: true
```

この宣言が揃ってから、`enter -> Foundation -> Recipe operations -> stop -> exit`
の順に進みます。生成物と実行状態の正は Business Pack の `work/` 配下です。
