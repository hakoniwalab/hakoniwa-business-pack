# Recipe実行ツール

このディレクトリには、特定のRecipeを構成・実行・検証するためのツールを置きます。
Foundation、Catalog、workspace全体を管理する汎用ツールは親の`tools/`に置き、
Recipe固有のプロセス構成、Docker構成、smoke testはここへ分離します。

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
