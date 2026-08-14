# Hakoniwa Local Foundation

Foundationは、複数のRecipeから再利用する箱庭共通基盤です。

現行設計では、次の二つだけを永続的な正として扱います。

- Recipeの`foundation_requirements`
- install済みComponentが生成するReceipt

評価結果の`SATISFIED`、`MISSING`、`INCOMPATIBLE`、`UNKNOWN`と、その理由は都度導出し、保存しません。

## Schema

最小データ契約は[`schema.yaml`](schema.yaml)で定義します。

Recipe requirementsの例:

```yaml
foundation_requirements:
  hakoniwa-core-pro:
    version:
      min: 1.0.0
    capabilities:
      shared_memory: true
      hako_cmd: true
    build_limits:
      asset_num:
        min: 16

  hakoniwa-pdu-bridge-core:
    capabilities:
      hakoniwa_app: true
      web_bridge: true
```

`version.min`は、Receiptの`component.version`に対する下限です。
機能の有無は`capabilities`で表します。`true`は機能が有効であること、
`false`は機能が無効であることを要求します。例えばCore-free構成では
`hakoniwa_core: false`を指定できます。特定リリース以降を必須とする外部契約が
ある場合だけ、`version.min`も併用します。

Component Receiptの標準配置:

```text
work/foundation/install/share/hakoniwa/receipts/<component-id>.yaml
```

ReceiptはComponentのinstall処理が生成します。Foundation Lock、Contract Hash、独立したInstall Contractは生成しません。

## 設定と証跡の流れ

```text
Recipe foundation_requirements
  -> work/foundation/build/<component-id>.yaml
  -> <component-repository>/.hako/resolved-build.yaml
  -> work/foundation/install/share/hakoniwa/receipts/resolved/<component-id>.yaml
  -> work/foundation/install/share/hakoniwa/receipts/<component-id>.yaml
```

Recipeは要求を所有し、Foundation resolverがComponent固有のbuild inputを生成します。Component repository内の`.hako/resolved-build.yaml`は一時情報であり、別の操作で上書きされ得ます。インストール済みFoundationを調査するときは、Receiptと、Receiptの`resolved_manifest`が指す保存済みmanifestを参照します。

責務とファイルの詳細は[`docs/hakoniwa-foundation-recipe-design-ja.md`](../docs/hakoniwa-foundation-recipe-design-ja.md#31-foundation設定と証跡のライフサイクル)を参照してください。

## Workspace

標準pathだけを確認する場合:

```bash
python tools/foundation.py paths --recipe-id drone-single-mujoco-threejs-mac
```

FoundationとRecipeの固定workspaceを準備する場合:

```bash
python tools/foundation.py prepare --recipe-id drone-single-mujoco-threejs-mac
```

`prepare`は同じdirectoryを再利用し、実行ごとのrun-id directoryを生成しません。

Python componentはRecipeごとのvenvを作らず、次のFoundation共通venvを利用します。

```text
work/foundation/install/python/
```

`hakoniwa-pdu`はこのvenvへ`pip install`し、`pip show hakoniwa-pdu`の`Location`がvenv内であることを確認します。Coreの`hakopy`とEndpoint CFFI packageも同じPython 3.12からimportできる構成にします。

Pythonのminor versionが同じだけでは、native extensionのABI整合を保証できません。
Foundation orchestratorを実行しているPythonでvenvを作成し、その同じinterpreterを
Core `hakopy`とEndpoint CFFIのbuildにも使用します。

`pip install`の成功表示だけでは、Foundation更新済みとは判断しません。
Foundation Pythonの`sys.executable`、`sys.prefix`、`pip show`の`Version`と
`Location`、必要moduleのsmoke importを確認します。Blender同梱Pythonや別venvへの
installはFoundationの状態ではありません。

Component buildは、同じbuild directoryに複数世代のinstall候補を残さないでください。
古い成果物を消す場合も、component自身が所有するpackage patternだけへ限定します。
version、Capability、Receipt、smoke、Catalog、Recipeを伴うリリース手順は
[`Foundation Component リリースチェックリスト`](../docs/foundation-component-release-checklist-ja.md)
を参照してください。

## Inspectorと構築

通常ユーザーとclean CIにおけるsource取得の入口は`tools/recipe.py`です。
Foundation ComponentとRecipe固有dependencyを一つのplanで確認し、missingなclone可能
repositoryをmaterializeしてからFoundationを構築します。

```bash
python tools/recipe.py plan --recipe <recipe.yaml>
python tools/recipe.py configure --recipe <recipe.yaml>
```

既存checkoutはユーザー所有のlocal inputとして再利用し、暗黙の`git pull`、`checkout`、
`reset`、置換、削除は行いません。revisionが固定されていないsourceは、planでも
`unpinned`として表示し、再現可能であるとは扱いません。

以下の`foundation.py`操作はComponent/Foundation maintainer向けの低レベル入口です。
source treeを自動cloneせず、build対象のsourceまたは`tools/hako.py`がない場合は、
副作用を開始する前に`recipe.py configure`を案内して停止します。

Windowsなどでvcpkgを明示的に選択する場合は、親shellの`VCPKG_ROOT`を
書き換えず、Foundation設定として`work/foundation/config/toolchain.json`へ保存します。

```bash
python tools/foundation.py toolchain \
  --recipe-id mujoco-turtlebot3-mbody \
  --vcpkg-root C:\\project\\vcpkg
```

Foundationが生成するEndpoint、RPC、Bridgeのcomponent manifestはこのpathを使用します。
component doctorは解決したvcpkg pathと必要headerを検証するため、ambientな別installを
誤って選択した場合も出力から確認できます。

Recipeの要求と現在のReceiptを比較するだけなら、`doctor`を使います。

```bash
python3.12 tools/foundation.py doctor \
  --recipe recipes/examples/drone-single-mujoco-threejs-mac.yaml
```

`doctor`は`SATISFIED`、`MISSING`、`INCOMPATIBLE`、`UNKNOWN`と構造化された理由を表示し、buildやinstallは行いません。

実行前の依存順序と再構築対象は`plan`で確認します。

```bash
python3.12 tools/foundation.py plan \
  --recipe recipes/examples/drone-single-mujoco-threejs-mac.yaml
```

Foundationを構築する場合:

```bash
python3.12 tools/foundation.py build \
  --recipe recipes/examples/drone-single-mujoco-threejs-mac.yaml
```

構築順序と対応operationは
[`catalog/foundation-components.json`](../catalog/foundation-components.json)
から取得します。各componentのbuildロジックは複製せず、owner repositoryの
`tools/hako.py`を呼び出します。

Drone Three.jsの標準順序は次の通りです。

```text
hakoniwa-core-pro
  -> hakoniwa-pdu-python（Foundation共通venv）
  -> hakoniwa-pdu-endpoint（同venvへCFFIをinstall）
  -> hakoniwa-pdu-bridge-core
```

全要求が`SATISFIED`の場合、`build`はcomponentのbuild/install commandを呼ばず、
Receiptを再評価して終了します。`UNKNOWN`は管理外installの可能性があるため、
自動上書きせず停止します。

既存Receiptを意図的に置き換える保守作業では、対象componentを明示します。
指定componentと既知の下流依存だけが再構築されます。

```bash
python3.12 tools/foundation.py plan \
  --recipe recipes/examples/drone-single-mujoco-threejs-mac.yaml \
  --force hakoniwa-core-pro

python3.12 tools/foundation.py build \
  --recipe recipes/examples/drone-single-mujoco-threejs-mac.yaml \
  --force hakoniwa-core-pro
```

`--force`は通常実行には不要です。`UNKNOWN`を含む管理外installを明示的に
置き換える場合や、toolchain変更後の再構築などに限定して使います。

## Drone Three.js Recipe

Foundation構築後、Recipe固有configとlauncherを生成します。

```bash
python3.12 tools/recipe/drone_threejs.py configure
python3.12 tools/recipe/drone_threejs.py doctor
```

生成物はすべて次に配置されます。

```text
work/recipes/drone-single-mujoco-threejs-mac/
```

起動とMission:

```bash
work/recipes/drone-single-mujoco-threejs-mac/launch.bash
work/recipes/drone-single-mujoco-threejs-mac/missions/run-single-mission.bash
```

終了時は、`launch.bash`を実行しているterminalで`Ctrl+C`を入力します。
Launcherが配下のDrone service、Visual-state publisher、WebBridge、HTTP serverを
まとめて終了します。この構成のlauncher終了手段として`hako-cmd stop`は使いません。

Recipe configuratorはDrone Core repositoryを変更しません。PDU、Fleet、
Visual-state、Bridge、Launcher設定をRecipe workspaceへ配置し、
FoundationのCore config、mmap、binary、library、共有Python venvを参照します。

## Validation

Recipe requirementsは既存Recipe validatorで検証します。

```bash
ruby recipes/tools/validate_recipes.rb
```

Receiptは次のコマンドで検証します。

```bash
ruby foundation/tools/validate_receipts.rb <receipt.yaml> [...]
```

引数を省略した場合は、標準Foundation workspaceのReceiptを検証します。

```text
work/foundation/install/share/hakoniwa/receipts/*.yaml
```
