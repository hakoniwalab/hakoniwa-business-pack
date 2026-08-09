# Recipe実行ツール

このディレクトリには、特定のRecipeを構成・実行・検証するためのツールを置きます。
Foundation、Catalog、workspace全体を管理する汎用ツールは親の`tools/`に置き、
Recipe固有のプロセス構成、Docker構成、smoke testはここへ分離します。

## 配置規約

`tools/`直下には、複数Recipeから共通利用する次の責務だけを置きます。

- Foundationの解決・検査・ローカルインストール
- Workspaceのenter/runと環境境界
- Catalogの診断
- Recipe YAMLからの汎用guide／portal生成
- OS共通doctorとplatform bootstrap

一つのRecipe、または密接に関連するRecipe群だけが利用するoperator、worker、
probe、platform adapter、unit testは`tools/recipe/`へ置きます。新しいツールが
`configure`、`build`、`doctor`、`start`、`smoke`、`status`、`stop`などの
Recipe固有ライフサイクルを実装する場合も、このディレクトリが配置先です。

移動や追加の際は、Recipe YAML、README、Knowledge、CIの参照パスを同時に
更新します。プロセスの所有権、session file、正常停止、残留確認の契約は、
ファイル配置の変更を理由に変えてはいけません。

## Recipe operator

- `drone_threejs.py`
- `drone_gamepad_exhibition.py`
- `drone_shibuya_gamepad.py`
- `shadow_hand_foxglove.py`
- `turtlebot3_godot_exhibition.py`
- `unitree_go1_demo.py`
- `ros2_service_add_two_ints.py`
- `ros2_bridge_examples.py`
- `hakoniwa_conductor.py`
- `hakoniwa_conductor_time_sync.py`

## Hakoniwa Conductor v1.0.0

公開バイナリの準備Recipeは、ライセンス確認後にOS/CPUに対応するRelease ZIPを
Business Packの`work/`へ取得し、SHA-256、プラットフォーム契約、11個の収録
バイナリを検証します。システムディレクトリにはインストールしません。

```bash
python tools/recipe/hakoniwa_conductor.py configure --accept-license
python tools/recipe/hakoniwa_conductor.py doctor
python tools/recipe/hakoniwa_conductor.py status
```

`--accept-license`は人間の判断境界です。AIが暗黙に付与してはいけません。
このRecipeはバイナリ準備だけを担当し、Conductorプロセスは起動しません。

生成済み設定を同梱した公開Python時刻同期サンプルは、別の実行Recipeで確認します。

```bash
python tools/recipe/hakoniwa_conductor_time_sync.py configure
python tools/recipe/hakoniwa_conductor_time_sync.py doctor
python tools/recipe/hakoniwa_conductor_time_sync.py smoke
```

`smoke`はConductor Server、2つのClient、2つのPython Assetを起動し、両Assetが
tick 20で同じ箱庭時刻を観測することを確認してから正常停止します。初回実行前に
`tools/foundation.py plan`で、公開バイナリのbuild contractと互換なFoundationを
準備してください。

補助worker、probe、platform adapterと対応する`test_*.py`も同じディレクトリで
管理します。CIは`tools/recipe/test_*.py`をdiscoverするため、新規テストを
個別列挙する必要はありません。

## ROS 2 Service / Action

既存のService Serverデモは次のコマンドを使います。

```bash
python tools/recipe/ros2_service_add_two_ints.py configure
python tools/recipe/ros2_service_add_two_ints.py build
python tools/recipe/ros2_service_add_two_ints.py doctor
python tools/recipe/ros2_service_add_two_ints.py start --approve-non-loopback-bind
python tools/recipe/ros2_service_add_two_ints.py smoke
python tools/recipe/ros2_service_add_two_ints.py status
python tools/recipe/ros2_service_add_two_ints.py stop
```

残り3方向は共通のprofile付きツールを使います。

```bash
python tools/recipe/ros2_bridge_examples.py service-client configure
python tools/recipe/ros2_bridge_examples.py action-server configure
python tools/recipe/ros2_bridge_examples.py action-client configure
```

各profileで利用できる操作は共通です。

```text
configure -> build -> doctor -> start -> smoke -> status -> stop
```

`start`はreadiness確認後に復帰し、Recipeはバックグラウンドで動作を継続します。
確認後は必ず同じprofileの`stop`を実行してください。

Action Recipeは`hakoniwa-pdu >= 1.6.6`を要求します。この版から
`hako_action_msgs`と`sample_action_msgs/Fibonacci`の生成済みPython型が
正式な`hakoniwa-pdu` packageに含まれます。
