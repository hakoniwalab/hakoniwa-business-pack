# Hakoniwa Local Foundation

Foundationは、複数のRecipeから再利用する箱庭共通基盤です。

初期実装では、次の二つだけを永続的な正として扱います。

- Recipeの`foundation_requirements`
- install済みComponentが生成するReceipt

評価結果の`SATISFIED`、`MISSING`、`INCOMPATIBLE`、`UNKNOWN`と、その理由は都度導出し、保存しません。

## Schema

最小データ契約は[`schema.yaml`](schema.yaml)で定義します。

Recipe requirementsの例:

```yaml
foundation_requirements:
  hakoniwa-core-pro:
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

Component Receiptの標準配置:

```text
work/foundation/install/share/hakoniwa/receipts/<component-id>.yaml
```

ReceiptはComponentのinstall処理が生成します。Foundation Lock、Contract Hash、独立したInstall Contractは生成しません。

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

## Inspectorと構築

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
python3.12 tools/drone_threejs.py configure
python3.12 tools/drone_threejs.py doctor
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
