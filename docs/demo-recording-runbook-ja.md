# デモ動画録画 Runbook

この runbook は、ローカルに構築済みの箱庭デモを動画素材として録画するための手順です。

対象デモ:

- FR5 アーム: MuJoCo viewer + JointTrajectory
- AgileX Tracer: MuJoCo viewer + `geometry_msgs/Twist`
- Unitree Go1: MuJoCo viewer + 12 関節 open-loop motion
- Drone: Three.js viewer + Python external RPC

## 録画方針

1 本の長い動画を一発撮りするのではなく、デモごとに別々に録画します。失敗したデモだけ撮り直せるため、編集と品質確認が楽になります。

推奨順:

```text
1. FR5 Arm
2. AgileX Tracer
3. Unitree Go1
4. Drone
```

最初の 3 つは MuJoCo viewer で見た目を揃え、最後に Three.js ブラウザ viewer のドローンで締めます。

## 画面構成

見栄え重視:

```text
viewer を全画面または大きく表示
terminal は開始数秒だけ見せる
```

説明重視:

```text
左: viewer
右: terminal
```

macOS では QuickTime Player の「新規画面収録」で十分です。編集時にデモ名、PDU 種別、制御方式を短いテロップで入れると伝わりやすくなります。

## 共通チェック

録画前に、必要な repo の状態と Python 環境を確認します。

```bash
cd /Users/tmori/project/business-pack/hakoniwa-business-pack
git status --short
```

各デモ repo でも確認します。dirty な repo は `git pull` しません。clean な repo だけ `git pull --ff-only` します。

```bash
cd /Users/tmori/project/business-pack/hakoniwa-mujoco-robots
git status --short

cd /Users/tmori/project/business-pack/hakoniwa-robot-arm-pack
git status --short

cd /Users/tmori/project/business-pack/hakoniwa-drone-core
git status --short
```

Python はこの環境では pyenv の 3.12 を使います。

```bash
/Users/tmori/.pyenv/shims/python3.12 -c "import hakopy; import hakoniwa_pdu; print('python ok')"
```

Hakoniwa runtime は、原則として次の順序で開始します。

```text
1. plant/simulator asset を起動し、asset 登録と WAIT START を確認
2. controller/sender asset を起動し、asset 登録と WAIT START を確認
3. /usr/local/hakoniwa/bin/hako-cmd start
```

`hako-cmd stop` には依存しません。録画後は、起動した asset/launcher の端末で `Ctrl-C` して終了します。Drone は launcher が子プロセスをまとめて cleanup します。

## FR5 Arm

目的:

- FR5 が `trajectory_msgs/JointTrajectory` を受け取り、pick-and-place 風の 16.5 秒モーションを実行する様子を録画する。

作業ディレクトリ:

```bash
cd /Users/tmori/project/business-pack/hakoniwa-robot-arm-pack
```

Terminal 1: FR5 asset + MuJoCo viewer

```bash
./build/bin/fr5-hakoniwa-asset
```

待つログ:

```text
hako_asset_register :FR5
asset(FR5) is registered.
WAIT START
```

Terminal 2: simulation start

```bash
/usr/local/hakoniwa/bin/hako-cmd start
```

Terminal 3: trajectory sender

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/fr5/send_joint_trajectory.py recipes/fr5/asset-manifest.json
```

成功サイン:

```text
Successfully sent JointTrajectory PDU.
[INFO] Accepted JointTrajectory: joints=6 points=9
```

録画見せ場:

- `approach`
- `pick pose`
- `wrist rotation`
- `base rotation while lifted`
- `return home`

終了:

- Terminal 1 で `Ctrl-C`
- プロセスが残っていないことを確認

```bash
ps -axo pid,ppid,stat,command | rg 'fr5-hakoniwa-asset|send_joint_trajectory|FR5'
```

## AgileX Tracer

目的:

- Rover asset が `geometry_msgs/Twist` を受け取り、Tracer が MuJoCo viewer 上で移動する様子を録画する。

作業ディレクトリ:

```bash
cd /Users/tmori/project/business-pack/hakoniwa-mujoco-robots
```

Terminal 1: plant asset + MuJoCo viewer

```bash
./src/cmake-build/examples/actuators/agilex_tracer/rover-twist-hakoniwa-asset
```

待つログ:

```text
hako_asset_register :RoverTwistAsset
asset(RoverTwistAsset) is registered.
[INFO] asset=RoverTwistAsset cmd_vel=cmd_vel
WAIT START
```

Terminal 2: Python Twist sender

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/actuators/agilex_tracer/send_rover_twist.py --linear-x 0.2 --duration-sec 4
```

待つログ:

```text
Rover Twist sender is registered.
WAIT START
```

Terminal 3: simulation start

```bash
/usr/local/hakoniwa/bin/hako-cmd start
```

成功サイン:

```text
Rover Twist Hakoniwa asset started.
cmd=(0.200, 0.000)
base x が増える
```

録画見せ場:

- viewer で初期姿勢を 1 秒見せる
- 前進を 3-4 秒見せる
- 必要なら sender に `--angular-z` を追加して旋回も撮る

終了:

- Terminal 1 で `Ctrl-C`
- sender は duration 後にゼロコマンドを送って終了

## Unitree Go1

目的:

- Menagerie Go1 が 12 関節 command PDU を受け取り、姿勢または open-loop motion を行う様子を録画する。

作業ディレクトリ:

```bash
cd /Users/tmori/project/business-pack/hakoniwa-mujoco-robots
```

録画候補:

- 安定重視: `walk_go1.py --duration-sec 6`
- 派手さ重視: `pose_bounce_go1.py --cycles 2`
- `--profile trot` は倒れやすいため、撮影本番では避けるか短く使う

### Go1 Walk

Terminal 1: plant asset + MuJoCo viewer

```bash
./src/cmake-build/examples/actuators/unitree_go1/unitree-go1-joint-hakoniwa-asset
```

Terminal 2: walk sender

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/actuators/unitree_go1/walk_go1.py --duration-sec 6
```

Terminal 3: simulation start

```bash
/usr/local/hakoniwa/bin/hako-cmd start
```

成功サイン:

```text
Go1 walk sender is registered.
sent walk phase=warmup
sent walk phase=creep
sent walk phase=done
```

注意:

- 現在の Go1 剛体/contact では、安定するのは後退方向の creep です。
- `--forward` は反対方向の試行用で、その場足踏みや不安定化が起きることがあります。
- 倒れる場合は小さくします。

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/actuators/unitree_go1/walk_go1.py --duration-sec 6 --frequency-hz 0.45 --thigh-amp 0.07 --calf-lift 0.08 --hip-sway 0.0
```

### Go1 Pose Bounce

Terminal 2 をこちらに替えます。

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/actuators/unitree_go1/pose_bounce_go1.py --cycles 2
```

成功サイン:

```text
Go1 pose bounce sender is registered.
phase=home
phase=crouch
phase=extend
phase=recover
```

表現上の注意:

- `pose_bounce` は jump-like posture demo であり、検証済みジャンプではありません。
- `walk_go1.py` は open-loop motion demo であり、検証済み歩行制御器ではありません。

終了:

- Terminal 1 で `Ctrl-C`

## Drone

目的:

- 1 台の Hakoniwa Drone を Python external RPC で `set_ready -> takeoff -> goto -> land` し、Three.js viewer で表示する。

作業ディレクトリ:

```bash
cd /Users/tmori/project/business-pack/hakoniwa-drone-core
```

事前に 8000 番を確認します。既存の `http.server 8000` が残っている場合は launcher と競合します。

```bash
ps -axo pid,ppid,stat,command | rg 'http.server 8000|mac-main_hako_drone_service|mac-drone_visual_state_publisher|hakoniwa-pdu-web-bridge|hako_launcher'
```

launcher 起動:

```bash
PYTHON_BIN=/Users/tmori/.pyenv/shims/python3.12 \
HAKO_DRONE_SERVICE_BIN=./lib/mac-main_hako_drone_service \
HAKO_VISUAL_STATE_PUBLISHER_BIN=./lib/mac-drone_visual_state_publisher \
HAKO_THREEJS_VIEWER_PATH=/Users/tmori/project/business-pack/hakoniwa-threejs-drone \
bash tools/launch-fleets-scale-perf.bash 1 "" 1
```

待つログ:

```text
hako-cmd start exited with 0
```

HTTP 確認:

```bash
curl -I http://127.0.0.1:8000/index.html
```

ブラウザを開く:

```bash
open 'http://127.0.0.1:8000/index.html?viewerConfigPath=/config/viewer-config-fleets.json&wsUri=ws://127.0.0.1:8765&wireVersion=v2'
```

ブラウザで `Connect` を押します。Connect 前に mission を実行しないでください。

mission 実行:

```bash
PATH=/Users/tmori/.pyenv/shims:$PATH \
bash drone_api/external_rpc/apps/run_single_mission.bash \
  --drone Drone-1 --alt 1.0 --x 1.5 --y 0.5 --z 1.0 --yaw 30 \
  --speed 1.0 --timeout-sec 20 --land
```

成功サイン:

```text
response ok=True message=ready
response ok=True message=completed
[single-mission] done
```

VisualStatePublisher 側の成功サイン:

```text
publish seq=... valid_count=1 first_drone_pos=(...)
```

終了:

- launcher の terminal で `Ctrl-C`
- `terminating all assets` と `state -> TERMINATED` を確認
- 残プロセス確認

```bash
ps -axo pid,ppid,stat,command | rg 'http.server 8000|mac-main_hako_drone_service|mac-drone_visual_state_publisher|hakoniwa-pdu-web-bridge|hako_launcher|run_single_mission'
```

## 撮影後チェック

各 demo 後に、起動したプロセスが残っていないことを確認します。

```bash
ps -axo pid,ppid,stat,command | rg 'fr5-hakoniwa-asset|rover-twist|unitree-go1|walk_go1|pose_bounce|mac-main_hako_drone_service|mac-drone_visual_state_publisher|hakoniwa-pdu-web-bridge|http.server 8000|hako_launcher'
```

生成物が増えた場合は、自分が作った一時ファイルか、既存の作業差分かを分けて判断します。ユーザーや既存作業の差分を戻さないでください。

```bash
git status --short
```

## よくある失敗

### `hako-cmd start` が早すぎる

症状:

- PDU が作成されない
- sender が command を送っても plant が動かない

対処:

- plant asset と sender asset の両方が `WAIT START` になってから `/usr/local/hakoniwa/bin/hako-cmd start`

### Python が `hakopy` を import できない

症状:

```text
ModuleNotFoundError: No module named 'hakopy'
```

対処:

```bash
/Users/tmori/.pyenv/shims/python3.12 -c "import hakopy; import hakoniwa_pdu"
```

この Python を sender/mission に使います。

### Drone viewer が開かない

確認:

```bash
curl -I http://127.0.0.1:8000/index.html
```

`200 OK` でなければ webserver が起動していません。8000 番の競合も確認します。

### Drone viewer に表示されるが動かない

確認:

- ブラウザで `Connect` を押したか
- WebBridge が `WAIT RUNNING` になっているか
- VisualStatePublisher が `valid_count=1` を publish しているか
- mission を Connect 後に実行したか

### Go1 が倒れる

撮影本番はデフォルト creep か小さめ設定を使います。

```bash
/Users/tmori/.pyenv/shims/python3.12 examples/actuators/unitree_go1/walk_go1.py --duration-sec 6 --frequency-hz 0.45 --thigh-amp 0.07 --calf-lift 0.08 --hip-sway 0.0
```

## 編集メモ

各デモのテロップ例:

```text
FR5 Arm: trajectory_msgs/JointTrajectory -> MuJoCo joint targets
AgileX Tracer: geometry_msgs/Twist -> differential wheel control
Unitree Go1: std_msgs/Float64MultiArray[12] -> joint position targets
Drone: Python external RPC -> Drone service -> Three.js visual state
```

各素材の推奨尺:

```text
FR5 Arm: 20-25 sec
AgileX Tracer: 8-12 sec
Unitree Go1: 8-12 sec
Drone: 15-20 sec
```

最終動画は 60-90 秒程度にまとめると、ビジネスデモとして見やすくなります。
