# Recipe実行ツール

このディレクトリには、特定のRecipeを構成・実行・検証するためのツールを置きます。
Foundation、Catalog、workspace全体を管理する汎用ツールは親の`tools/`に置き、
Recipe固有のプロセス構成、Docker構成、smoke testはここへ分離します。

## Drone Fleet性能測定

ブラウザ表示用のDrone Fleetデモとは分離し、可視化なし、実時間同期なしで
測定します。単一条件の確認では、Business Pack workspaceへ入ってから次を
実行します。

```bash
python3.12 tools/recipe/drone_fleet_performance.py configure
python3.12 tools/foundation.py plan \
  --recipe work/recipes/drone-fleet-single-process-scaling/config/foundation-requirements.yaml
python3.12 tools/foundation.py build \
  --recipe work/recipes/drone-fleet-single-process-scaling/config/foundation-requirements.yaml
python3.12 tools/recipe/drone_fleet_performance.py doctor
python3.12 tools/recipe/drone_fleet_performance.py start
python3.12 tools/recipe/drone_fleet_performance.py smoke
python3.12 tools/recipe/drone_fleet_performance.py stop
```

`start`は選択されたattemptディレクトリだけを消してから起動するため、以前の
raw sampleが新しい試行へ混入しません。最小パスの結果は次へ保存されます。

```text
work/recipes/drone-fleet-single-process-scaling/results/
  single-process-scaling/uav-001-proc-01/attempt-01/
    preflight-machine-samples.jsonl
    machine-samples.jsonl
    result.json
```

全アセット起動後、Takeoff投入前に、測定区間とは独立したマシン負荷の
preflightを行います。CPU使用率とメモリ使用量・使用率のraw sampleは
`preflight-machine-samples.jsonl`、集約値は`result.json`の
`machine_preflight`へ保存します。実験YAMLの
`invalid_conditions.preflight_max_cpu_average_percent`または
`invalid_conditions.preflight_max_memory_used_percent`を超えた試行は`invalid`となり、性能値を
採用しません。preflightの実時間はRTFや1ステップ平均実行時間には含みません。
CPU使用率は累積カウンタの差分を必要とするため、測定区間がOSのカウンタ更新
周期より短い場合は`cpu_sample_count: 0`、CPU集約値は`null`となり、その試行を
validationで無効とします。監視周期を過度に短くして測定対象へ負荷を加えるのでは
なく、本測定ではCPUサンプルが得られるだけの測定区間を確保してください。

測定区間はTakeoff投入から始まります。Formation完了後、`stop_conditions`の最小virtual time、CPU有効
サンプル数、マシンサンプル数をすべて満たした時点で終了します。条件未達のまま
`invalid_conditions`のvirtual-timeまたはwall-time上限へ達した試行は無効です。
Fleet処理と測定全体の合否は、次のログだけでも確認できます。

```bash
grep '^RESULT:' \
  work/recipes/drone-fleet-single-process-scaling/logs/show-runner.out
```

各phaseには対象機数、成功数、失敗数、失敗した機体名が出力され、最後の
`drone_fleet_performance`行はFleet、測定、validationをまとめて`PASS`または
`FAIL`で示します。同じphase集約は`result.json`の
`metadata.fleet_phase_results`にも保存されます。

### Experiment A：機体数の連続測定

単一シミュレータプロセスのまま機体数だけを変える系列は、実験YAMLの
`matrix.drone_count`を順番に実行します。

```bash
python3.12 tools/recipe/drone_fleet_performance_a.py plan
python3.12 tools/recipe/drone_fleet_performance_a.py run
python3.12 tools/recipe/drone_fleet_performance_a.py summarize
```

`run`は各条件について既存operatorの`configure -> doctor -> start -> smoke -> stop`
を実行します。既存の`result.json`が一つでもある場合、通常実行は暗黙の上書きを
避けるため開始しません。中断後は次で、記録済みattemptを保存してスキップします。

```bash
python3.12 tools/recipe/drone_fleet_performance_a.py run --resume
```

マシン負荷preflightが失敗した場合は、その後の条件も比較不能になるため系列全体を
停止します。個別条件の測定・validation失敗は結果を保存し、後続条件を継続します。
集約結果は次に生成されます。

```text
work/recipes/drone-fleet-single-process-scaling/results/
  single-process-scaling/summary/
    experiment-a.json
    experiment-a.csv
```

集約後、RTF、1ステップ平均実行時間、CPU、メモリを独立した縮尺の4区画へ
まとめた1枚のSVGを生成できます。RTFとステップ時間には対数縮尺を使用するため、
小規模条件と大規模条件を同じ図で比較できます。

```bash
python3.12 tools/recipe/drone_fleet_performance_plot.py
```

生成後は次のSVGをmacOS、Linux、またはWindows側のブラウザ／画像ビューアで開きます。

```text
work/recipes/drone-fleet-single-process-scaling/results/
single-process-scaling/summary/plots/scaling-overview.svg
```

### Experiment B：プロセス数の連続測定

同一ホスト上で128機の論理ワークロードを固定し、シミュレータプロセス数だけを
`1, 2, 4, 6, 8, 12, 15`へ変える系列です。Experiment Aと同じ組み込み
Conductorを使用し、外部Conductor PROはマルチホストのExperiment Cから使用します。
各条件では128機をプロセス間へ可能な限り均等に静的分割します。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py plan
python3.12 tools/recipe/drone_fleet_performance_b.py run
python3.12 tools/recipe/drone_fleet_performance_b.py summarize
```

`run`は系列全体を前景で管理します。別端末から現在のLauncher sessionを確認・停止
する場合は、`status`または`stop`を使用できます。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py status
python3.12 tools/recipe/drone_fleet_performance_b.py stop
```

中断後に記録済みattemptを保持して再開する場合は、次を使用します。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py run --resume
```

Experiment Bのマシン負荷preflightは、プロセス数そのものの負荷を外部負荷と
誤認しないよう、各条件のDrone Serviceを起動する前に実施します。不合格または
failedのattemptは`--resume`だけでは再利用しません。該当attemptを`rejected/`へ
退避して再測定し、成功済み条件から続ける場合は次を使用します。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py run \
  --resume --rerun-invalid
```

集約結果は`work/recipes/drone-fleet-multi-process-scaling/results/`以下に保存され、
各プロセス数の中央値、最小・最大ステップ時間、相対ばらつき、追加試行の要否を
`experiment-b.json`と`experiment-b-aggregate.csv`へ記録します。現在の
各条件は3試行します。各試行の開始前には、最大60秒の範囲で1秒preflight窓を
繰り返し、設定されたCPU・メモリ閾値内へ落ち着いてからAssetを起動します。
ステップ時間の相対ばらつきが5%を超えるかfailureを含む条件だけ5試行へ増やします。
必要回数が揃うまでは代表プロセス数を選定せず、
`selection_status: additional_runs_required`とします。測定契約を変更して全条件を
取り直す場合は、既存系列を削除せず退避する`--restart-series`を使用します。

3試行の集計後、追加対象だけattempt 4・5を実行するには`extend`を使用します。
`extend`は初期3試行を変更せず、`escalation_required: true`のプロセス数だけを
追加測定して5試行で再集計します。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py extend
```

各attemptの開始前には、前回のnative Drone Serviceが残っていないことも検査します。
Launcherが`TERMINATED`を返した後も今回起動したプロセスが残った場合は、PID差分で
Recipe所有プロセスだけを終了し、実プロセスの消滅を確認してから次attemptへ進みます。

```bash
python3.12 tools/recipe/drone_fleet_performance_b.py run --restart-series
```

```bash
python3.12 tools/recipe/drone_fleet_performance_plot.py \
  work/recipes/drone-fleet-multi-process-scaling/results/\
multi-process-scaling/summary/experiment-b.json \
  --x-field process_count
```

生成後は次のSVGをmacOS、Linux、またはWindows側のブラウザ／画像ビューアで開きます。

```text
work/recipes/drone-fleet-multi-process-scaling/results/
multi-process-scaling/summary/plots/scaling-overview.svg
```

Experiment Bのperformance runは公平性のため時刻observerを無効にします。性能系列の
完了後、128機の2プロセスと15プロセスについて、専用Temporal Validationを実行します。

```bash
python3.12 tools/recipe/drone_fleet_temporal_b.py plan
python3.12 tools/recipe/drone_fleet_temporal_b.py run
python3.12 tools/recipe/drone_fleet_temporal_b.py summarize
```

時刻観測結果はperformance系列と混在させず、`single-host-temporal-validation/`へ
保存します。各runは`temporal-samples.jsonl`、lagのmedian / p95 / maximum、
accepted / rejected sample数、acceptance ratioを記録します。Temporal Validationの
wall-clock値はExperiment Bの代表性能値には使用しません。

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
- `drone_fleet_single_host.py`
- `drone_fleet_multi_process.py`
- `drone_fleet_performance_a.py`
- `drone_fleet_performance_b.py`
- `drone_fleet_temporal_b.py`
- `shadow_hand_foxglove.py`
- `turtlebot3_godot_exhibition.py`
- `mujoco_turtlebot3_mbody.py`
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

## TurtleBot3 MBody / MuJoCo

`mujoco-turtlebot3-mbody.yaml`はmacOSとWindowsで共通です。OS別Recipeや
OS別の公開手順は作らず、次の同じ操作を使用します。

```bash
python tools/recipe/mujoco_turtlebot3_mbody.py configure
python tools/recipe/mujoco_turtlebot3_mbody.py build
python tools/recipe/mujoco_turtlebot3_mbody.py doctor
python tools/recipe/mujoco_turtlebot3_mbody.py start
python tools/recipe/mujoco_turtlebot3_mbody.py status
python tools/recipe/mujoco_turtlebot3_mbody.py stop
```

既定は`--model burger`です。WaffleとWaffle Piも同じRecipeと操作列を使い、各操作へ
それぞれ`--model waffle`または`--model waffle_pi`を指定します。

Windowsのvcpkg選択、実行ファイル拡張子、multi-config build配置、MSVC import
library検査と、macOS/Linuxのloader pathはrunner内部で吸収します。生成物は
どちらも`work/recipes/mujoco-turtlebot3-mbody/`配下に置かれます。

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

## Native Single-host Multi-drone

任意の機体数を複数のDrone simulator processへ分割するRecipeは、実験条件を
YAMLで受け取ります。現在のMVP既定値は200機、4 processです。
`scale.drone_count: 200`と`scale.drones_per_process: auto`を維持したまま、
`scale.process_count`だけを1〜20へ変更することで、総機体数を固定した比較実験を
実行できます。均等に割り切れない機体は最後のprocessへ割り当てます。
このとき末尾側の各processへ1機ずつ割り当て、process間の差を最大1機に保ちます。
総機体数をprocess形状から求める`drone_count: auto`や、process数を求める
`process_count: auto`も利用できます。設定変更後は`configure`が必要であり、
`start`は内部で`doctor`を実行して、実験YAMLと生成済みpartitionが一致しなければ
起動を拒否します。
既定の`asset_num=16`では、Single-host構成の上限は可視化なしで15 process、
可視化ありで13 processです。内訳はDrone simulator processにShowRunnerを加え、
可視化時だけVSPとWebBridgeを加えたものです。最初のDrone simulatorが内蔵Conductorを
所有するため、独立したConductor Clientは数えません。これを超える場合は、同じbuild
limitで揃えたFoundationとDroneバイナリが必要です。
MVPはDockerを使わず、macOS / Linux / Windowsのnative
Drone binaryを選択します。全機の離陸完了を待ってからHAKONIWAの文字配置へ
移り、全機の配置完了後に一定時間保持します。実行可能な最小数は1機です。
1〜25機では26本のストロークを全体から等間隔に選ぶため、完全な文字にはなりません。
26機で全ストロークを構成でき、視認性を確保する推奨値は52機以上です。
26機以上では各ストロークへ最低数を割り当て、残りの機体はストローク長に比例して
配分します。

一般ユーザ向けの公開／既定Droneバイナリ構成は最大200機です。201機以上を指定すると
`configure`は実行可能な生成物を作る前にエラーにします。201〜512機の研究評価には、
Core、Drone simulator、VSP、Endpoint、Bridgeを同一の512機向けbuild limitで作成し、
その整合性を確認できる専用プロファイルに加え、Hakoniwa Drone PROのライセンスと
非公開PROソースへのアクセス権が必要です。Foundationの値だけを増やして
公開バイナリを流用してはいけません。また200機超を含む性能比較では、公平性のため、
200機以下の測定点にも同じ512機向けバイナリプロファイルを使用します。

```bash
python tools/recipe/drone_fleet_single_host.py prepare-native
python tools/recipe/drone_fleet_single_host.py prepare-viewer
python tools/recipe/drone_fleet_single_host.py configure
python tools/foundation.py doctor \
  --recipe work/recipes/drone-fleet-single-host/config/foundation-requirements.yaml
python tools/recipe/drone_fleet_single_host.py doctor
python tools/recipe/drone_fleet_single_host.py start
python tools/recipe/drone_fleet_single_host.py open-viewer
python tools/recipe/drone_fleet_single_host.py smoke
python tools/recipe/drone_fleet_single_host.py stop
```

`prepare-native`は利用者が明示的に実行する取得操作です。兄弟ディレクトリの
`hakoniwa-drone-core`がなければ公式公開リポジトリの`v4.0.0`をcloneし、OSに対応する
公式ZIPを取得します。配布物のSHA-256を検証してから展開し、既に必要なバイナリが
存在する場合は何も変更しません。`doctor`がnative Drone serviceまたは
visual-state publisherの欠落を報告した場合も、この操作後に`doctor`を再実行します。

入力は`recipes/experiments/drone-fleet-single-host-mvp.yaml`、生成物はすべて
`work/recipes/drone-fleet-single-host/`配下です。`start`は背景起動後に復帰するため、
`open-viewer`でブラウザ表示を開き、`smoke`で全機のHAKONIWA配置と保持の完了を
確認し、最後に必ず`stop`してください。既定では着陸せず、文字を表示した状態で
実験シーケンスだけが完了します。`open-viewer`は解決済み総機体数を
`maxDynamicDrones`としてURLへ自動設定します。
`prepare-viewer`はThree.js Viewerをrecursive cloneし、既存cloneでは
`git submodule update --init --recursive`を実行します。`doctor`はViewer本体だけでなく、
ブラウザがimportするPDU JavaScriptも検査します。`open-viewer`はブラウザを自動起動せず、
開くべきURLを表示します。WSL2ではそのURLをWindows側ブラウザへコピーしてください。
通常のWSL localhost転送によりHTTPの8000番とWebSocketの8765番へ接続します。
各実行のwall/Core phase時間とReal Time Factorは
`validation/execution-summary.json`へ記録されます。matrix展開、反復実行、
実験ごとの結果保持、集計済み`results/`は次段階です。
