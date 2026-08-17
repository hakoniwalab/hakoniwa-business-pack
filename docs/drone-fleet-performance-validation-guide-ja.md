# Drone Fleet 性能検証・再現ガイド

## 1. 目的

この文書は、Drone Fleetの性能検証を第三者が再現し、測定条件、raw result、
集計値、グラフ、および評価資料に記載する数値を追跡できるようにするための
操作・データ仕様である。

対象は次の3系列とする。

| 系列 | 評価する軸 | 実行形態 |
| --- | --- | --- |
| Experiment A | 1 processのままUAV数を増やしたときのscale-up | single host / single process |
| Experiment B | UAV数ごとにprocess数を増やしたときの回復と飽和 | single host / multi process |
| Experiment C | 2 hostへ負荷を分割したときのscale-out | multi host / multi process |

Experiment BとCは、observerを無効にしたPerformance runだけでは完了しない。
observerを有効にしたTemporal Validationを別seriesで実施し、処理性能と時間整合性を
独立して評価する。

本書は、実装済み機能を **AVAILABLE**、仕様だけを先に定義した機能を
**PLANNED** と表記する。PLANNEDのコマンドは現時点では実行してはならない。

## 2. 正本と責務

同じ数値やパスを複数の文書へ手作業で複製しない。正本は次のように分ける。

| 情報 | 正本 |
| --- | --- |
| UAV数、process数、attempt、scenario、時間設定、invalid条件 | `recipes/experiments/drone-fleet-performance/*.yaml` |
| source、collection、analysisの配置 | `configs/result-layouts/drone-fleet-performance.yaml` |
| Recipeの構成、Foundation要求、外部source | `recipes/examples/drone-fleet-*.yaml` |
| 実行、再開、停止、集計の操作 | 本書および各operatorの`--help` |
| reportとdatasetの対応 | 各reportの`*-manifest.json` |
| 評価資料の図表IDとreportの対応 | PLANNED: report mapping manifest |

Experiment YAMLが測定条件のauthorityであり、本書の表はその読み方を説明する。
値が異なる場合はYAMLを優先し、validatorと文書を同じ変更で更新する。

### 2.1 Experimentと実装の対応

| 系列 | mode | Experiment YAML | runner |
| --- | --- | --- | --- |
| A | performance | `single-process-scaling.yaml` | `drone_fleet_performance_a.py` |
| B | performance | `multi-process-scaling.yaml` | `drone_fleet_performance_b.py` |
| B | temporal | `single-host-temporal-validation.yaml` | `drone_fleet_temporal_b.py` |
| C | performance | `multi-host-scaling.yaml` | `multi_host_scaling_attempt.py` |
| C | temporal | `multi-host-temporal-validation.yaml` | `multi_host_scaling_attempt.py` |

Bの2 modeは`drone-fleet-multi-process-scaling.yaml`、Cの2 modeは
`drone-fleet-multi-host.yaml`の構成を共有する。

## 3. Repository取得と共通セットアップ

### 3.1 Business Pack

対象machineごとに同じBusiness Pack revisionを使用する。

```bash
git clone https://github.com/hakoniwalab/hakoniwa-business-pack.git
cd hakoniwa-business-pack
git rev-parse HEAD
python3 tools/workspace.py enter
```

以降のコマンドは、明記がない限りBusiness Pack rootのmanaged Workspace内で
実行する。Python、native library、Foundation prefixをambient環境へ逃がさない。

### 3.2 Experiment A / BのFoundationとnative runtime

Aを実行するmachineでは次を実行する。

```bash
python tools/recipe.py configure \
  --recipe recipes/examples/drone-fleet-single-process-scaling.yaml
python tools/recipe/drone_fleet_single_host.py prepare-native
python tools/recipe.py doctor \
  --recipe recipes/examples/drone-fleet-single-process-scaling.yaml
```

Bを実行するmachineでは次を実行する。同じcheckoutでAを構成済みの場合、
Foundation receiptを検査し、満たされたcomponentは再利用される。

```bash
python tools/recipe.py configure \
  --recipe recipes/examples/drone-fleet-multi-process-scaling.yaml
python tools/recipe/drone_fleet_single_host.py prepare-native
python tools/recipe.py doctor \
  --recipe recipes/examples/drone-fleet-multi-process-scaling.yaml
```

`prepare-native`はDrone Coreのsource contractを読み、対象OSのnative distributionと
MuJoCo runtimeを準備する。MuJoCo versionを本書や呼び出し側へハードコードしない。

### 3.3 Experiment Cの追加セットアップ

Cでは両hostに同じBusiness Pack revision、互換なFoundation、Drone native runtime、
公開Hakoniwa Conductor v1.1.0 binary package、および設定生成用のConductor PRO
checkoutが必要である。Conductor PROは既定ではBusiness Packのsibling
`../hakoniwa-conductor-pro`として解決されるが、そのbuild成果物は実行には使わない。

各hostで公開版のlicenseを確認し、binary packageを準備する。

```bash
python3 tools/recipe/hakoniwa_conductor.py configure \
  --version v1.1.0 --accept-license
```

続いて、承認済みの生成用checkoutを配置してから次を実行する。

```bash
python tools/recipe.py configure \
  --recipe recipes/examples/drone-fleet-multi-host.yaml
python tools/recipe/drone_fleet_single_host.py prepare-native
```

Conductor PRO checkoutでは次を実行する。

```bash
python tools/hako.py build
```

古いCMake cacheが別のFoundation prefixを保持している場合は、新規build directoryで
再構成する。既存build directoryを削除するかは、そのcheckoutの所有者が内容を確認して
判断する。Business Packはprivate checkoutを自動更新・resetしない。

両hostの最低限の固定情報は次である。

| host ID | 役割 | 実行環境 | 物理host address |
| --- | --- | --- | --- |
| `srv-01` | server/listener | macOS native | `192.168.2.100` |
| `cli-01` | outbound client | WSL2/Linux | Windows host `192.168.2.104` |

WSL2 private addressは設定authorityにせず、WSL2からserverへoutbound接続する。

## 4. 測定前の共通条件

測定前に次を固定・記録する。

- Business Pack、Drone Core、Foundation component、Conductorのrevision
- OS、CPU、logical CPU数、memory、実行環境
- wired networkとaddress（Cのみ）
- Experiment YAMLのSHA-256またはGit revision
- `measurement.protocol_status`
- background loadを判定するpreflight結果

測定中にYAML、binary、process policy、host placementを変更した場合、同じseriesの
継続attemptとして扱わない。変更前datasetを退避し、新しいseriesとして開始する。

## 5. Experiment A

### 5.1 条件確認

```bash
python tools/recipe/drone_fleet_performance_a.py plan
```

`matrix.drone_count`の全条件と、記録済み/PENDINGのattemptを確認する。

### 5.2 実行と再開

```bash
python tools/recipe/drone_fleet_performance_a.py run
```

中断後、成功済みresultを保持して続行する場合だけ次を使う。

```bash
python tools/recipe/drone_fleet_performance_a.py run --resume
```

実行中のLauncherを手動停止する必要がある場合は次を使う。

```bash
python tools/recipe/drone_fleet_performance.py stop
```

### 5.3 集計

```bash
python tools/recipe/drone_fleet_performance_a.py summarize
```

出力は次である。

```text
work/recipes/drone-fleet-single-process-scaling/results/
└── single-process-scaling/
    ├── uav-NNN-proc-01/attempt-NN/
    │   ├── result.json
    │   ├── machine-samples.jsonl
    │   └── preflight-machine-samples.jsonl
    └── summary/
        ├── experiment-a.json
        └── experiment-a.csv
```

## 6. Experiment B

### 6.1 条件確認

```bash
python tools/recipe/drone_fleet_performance_b.py plan
```

`matrix.workloads`は明示的なsparse gridである。UAV数とprocess数の直積を暗黙生成しない。

### 6.2 baseline実行と再開

```bash
python tools/recipe/drone_fleet_performance_b.py run
```

```bash
python tools/recipe/drone_fleet_performance_b.py run --resume
```

invalid/failed attemptを退避して、その条件だけ再測定する場合は次を使う。

```bash
python tools/recipe/drone_fleet_performance_b.py run \
  --resume --rerun-invalid
```

測定契約を変更し、既存seriesを保存したまま全条件を開始し直す場合は次を使う。

```bash
python tools/recipe/drone_fleet_performance_b.py run --restart-series
```

### 6.3 extension

baseline集計後、ばらつきまたはfailure条件に該当したconfigurationだけattempt 4/5を
追加する。

```bash
python tools/recipe/drone_fleet_performance_b.py extend
```

extension判定はoperatorが生成するsummaryをauthorityとする。結果を見て手作業で
都合のよいconfigurationだけ追加しない。

### 6.4 集計

```bash
python tools/recipe/drone_fleet_performance_b.py summarize
```

```text
work/recipes/drone-fleet-multi-process-scaling/results/
└── multi-process-scaling/
    ├── uav-NNN-proc-NN/attempt-NN/
    │   ├── result.json
    │   ├── machine-samples.jsonl
    │   └── preflight-machine-samples.jsonl
    └── summary/
        ├── experiment-b.json
        ├── experiment-b.csv
        └── experiment-b-aggregate.csv
```

### 6.5 Temporal Validation

Performance run完了後、128 UAVのprocess分割における時間整合性を、observer有効の
別seriesで検証する。現在のvalidation endpointは2 processと15 process、各1 attemptである。

```bash
python tools/recipe/drone_fleet_temporal_b.py plan
python tools/recipe/drone_fleet_temporal_b.py run
python tools/recipe/drone_fleet_temporal_b.py summarize
```

中断後は記録済みendpointを保持して再開できる。

```bash
python tools/recipe/drone_fleet_temporal_b.py run --resume
```

出力はperformance seriesと分離する。

```text
work/recipes/drone-fleet-multi-process-scaling/results/
└── single-host-temporal-validation/
    ├── temporal-uav-128-proc-02/attempt-01/
    ├── temporal-uav-128-proc-15/attempt-01/
    └── summary/
        ├── temporal-b.json
        └── temporal-b.csv
```

記録する主な指標はlag median/p95/maximum、accepted/rejected sample数、acceptance
ratioである。Temporal ValidationのRTFはobserver overheadを含むため、Experiment Bの
performance summaryや代表RTFへ混ぜない。

現時点のendpoint process数`[2, 15]`はrunnerの`PROCESS_COUNTS`が所有している。
測定条件をYAMLへ一元化する原則に合わせ、測定完了後に
`single-host-temporal-validation.yaml`のmatrixへ移し、runnerをその入力へ接続する。
これは **PLANNED** であり、進行中の測定条件は変更しない。

## 7. Experiment C

### 7.1 実行profile

現在のremote-operation profileは自動化preflight用である。

```text
configs/remote-operation/multi-host-scaling-attempts.yaml
```

このprofileはExperiment YAMLの64/128/256 UAVを1接続で順に実行し、各条件のbaseline
attempt 1..3を測定する。failureまたはRTF relative spreadが宣言閾値を超えた条件だけ
attempt 4/5へ進む。

正式な再測定では、同じschemaを使い、`workspace.output_root`をresult layoutの
canonical C workspaceへ向けた専用profileを作成する。このprofileは **PLANNED** であり、
現在のpreflight測定中に切り替えない。

### 7.2 実行

Mac serverを先に開始する。

```bash
python tools/workspace.py run -- \
  python3 -m tools.remote_operation.multi_host_scaling_attempt \
  --profile configs/remote-operation/multi-host-scaling-attempts.yaml \
  server
```

続いてWSL2 clientを開始する。

```bash
python tools/workspace.py run -- \
  python3 -m tools.remote_operation.multi_host_scaling_attempt \
  --profile configs/remote-operation/multi-host-scaling-attempts.yaml \
  client
```

両processはcontrol connectionを維持し、condition/attemptごとにfresh Launcherを起動する。
`cli-01` resultとremote-operation evidenceはverified ZIPとしてserverへ転送される。

### 7.3 集計

remote-operation serverは各条件のsummaryと全体matrix summaryを自動生成する。

```text
<output-root>/results/<series>/summary/
├── multi-host-scaling-sleep-NNNms-uav-064.json
├── multi-host-scaling-sleep-NNNms-uav-128.json
├── multi-host-scaling-sleep-NNNms-uav-256.json
├── multi-host-scaling-sleep-NNNms-matrix.json
└── 対応するCSV
```

host別raw resultは次に置く。

```text
<output-root>/results/<series>/hosts/
├── srv-01/uav-NNN-sleep-NNNms/attempt-NN/
└── cli-01/uav-NNN-sleep-NNNms/attempt-NN/
```

### 7.4 Temporal Validation

Performance matrixとは別に、worst-caseとして宣言した256 UAV / 1 attemptで両hostの
時間整合性を検証する。使用するprofileは次である。

```text
configs/remote-operation/multi-host-temporal-validation.yaml
```

Mac serverを先に開始する。

```bash
python tools/workspace.py run -- \
  python3 -m tools.remote_operation.multi_host_scaling_attempt \
  --profile configs/remote-operation/multi-host-temporal-validation.yaml \
  server
```

続いてWSL2 clientを開始する。

```bash
python tools/workspace.py run -- \
  python3 -m tools.remote_operation.multi_host_scaling_attempt \
  --profile configs/remote-operation/multi-host-temporal-validation.yaml \
  client
```

remote-operation serverはverified client resultを回収し、次を含むpaired summaryを生成する。

- 両hostのlag median/p95/maximum
- accepted/rejected sample数とacceptance ratio
- 両hostのmeasurement start/end virtual-time差
- observer有効性、host/configuration/attempt/config hashの一致

```text
<output-root>/results/multi-host-temporal-validation/
├── hosts/
│   ├── srv-01/temporal-uav-256-sleep-001ms/attempt-01/
│   └── cli-01/temporal-uav-256-sleep-001ms/attempt-01/
└── summary/
    ├── multi-host-temporal-sleep-001ms-uav-256.json
    └── multi-host-temporal-sleep-001ms-uav-256.csv
```

Temporal Validationの性能値はExperiment Cのperformance matrixへ混ぜない。Temporal
summaryがcompleteで、両hostのaccepted sampleが1件以上あり、identityとvirtual-time
boundaryが揃うことをPerformance dataset採用前のgateとする。

## 8. Result収集とcanonical配置

配置authorityは次である。

```text
configs/result-layouts/drone-fleet-performance.yaml
```

現在のcanonical配置は次のとおりである。

| 系列 | producer | collection destination |
| --- | --- | --- |
| A | `mac`, `wsl2` | `exp-results/<machine>/single-process-scaling` |
| B | `mac`, `wsl2` | `exp-results/<machine>/multi-process-scaling` |
| B Temporal | `mac`, `wsl2` | `exp-results/<machine>/single-host-temporal-validation` |
| C | `srv-01`, `cli-01` | server workspace内の`<series>/hosts/<host-id>` |
| C Temporal | `srv-01`, `cli-01` | server workspace内のtemporal `<series>/hosts/<host-id>` |

A/Bは各machineが同じlocal pathを持つため、収集時にmachine dimensionを追加する。
Cはraw result自身がhost dimensionを持つため、その構造を維持する。

### 8.1 AVAILABLE: 共通result transfer tool

共通transfer toolは、利用者がsource/destinationを直接入力する方式にしない。
`result-layout`、transfer group、producer IDから両方を解決する。B/Cのgroupは
PerformanceとTemporal Validationを1つのZIPで扱う。

CLI contractは次である。

```text
python -m tools.remote_operation.result_transfer \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  --group <experiment-a|experiment-b|experiment-c> \
  --producer <wsl2|cli-01> \
  receive|send
```

Mac receiverを先に起動する。session IDは両hostで完全に一致させる。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.result_transfer \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  --group experiment-b \
  --producer wsl2 \
  --session-id result-b-wsl2-20260817-01 \
  receive --listen-address 192.168.2.100
```

続いてWSL2 senderを起動する。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.result_transfer \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  --group experiment-b \
  --producer wsl2 \
  --session-id result-b-wsl2-20260817-01 \
  send --server-address 192.168.2.100
```

Aは`--group experiment-a`を使用する。Cのclient artifactを独立収集するときは
`--group experiment-c --producer cli-01`を使用する。
ただしmulti-host attempt runnerが既にclient artifactを同じcanonical destinationへ
配置済みの場合、hashが完全一致するdatasetは`skipped_existing_identical`となる。

個別seriesだけを診断・転送するときは、従来どおり`--experiment <id>`も使用できる。

toolは次を必須とする。

1. sourceをZIP化し、全fileのrelative path、size、SHA-256をmanifestへ記録する。
2. PDU artifact transportで送信し、receiverがZIP全体のSHA-256を検証する。
3. stagingへ安全に展開し、absolute path、`..`、symlinkを拒否する。
4. experiment、series、producer、configuration/attempt identityを検証する。
5. source directoryが存在しないgroup memberは`skipped_missing_source`として記録する。
6. destinationが既存で全file hashが一致すればSKIPし、異なれば上書きせず停止する。
7. group内の新規destinationは、全datasetの検証完了後にまとめてpublishする。
8. sender/receiver双方へmachine-readableなtransfer evidenceを残す。

manifest schemaは次を正本とする。

```text
schemas/remote-operation/result-transfer-manifest.schema.json
```

転送evidenceとZIPは次へ保存する。

```text
work/remote-operation/result-transfer/<session-id>/
├── <group-id>-<producer-id>.zip
├── sender-result.json / receiver-result.json
├── sender-events.jsonl / receiver-events.jsonl
└── sender-endpoint/ / receiver-endpoint/
```

receiverはarchive全体のhash検証だけでは成功を返さない。manifest、各payload file、
Experiment/layout hash、result内のseries/configuration/attempt/host identityを検証し、
canonical destinationへのgroup publishが完了してから`VERIFIED`を返す。sourceが存在するが
summary不完全、identity不一致、hash不一致の場合はSKIPせずgroup全体を失敗させる。

## 9. グラフ生成

### 9.1 AVAILABLE: 単一datasetのscaling overview

既存のdependency-free SVG rendererは次である。

```text
tools/recipe/drone_fleet_performance_plot.py
```

RTF、平均step wall-clock時間、whole-machine CPU、whole-machine memoryを4 panelで描画し、
invalid、validation失敗、preflight失敗のresultを除外する。

#### Experiment A

```bash
python tools/recipe/drone_fleet_performance_plot.py
```

既定出力：

```text
work/recipes/drone-fleet-single-process-scaling/results/
single-process-scaling/summary/plots/scaling-overview.svg
```

#### Experiment B

Bは複数UAV workloadを含むため、`--drone-count`と個別の`--output`を必須とする。
出力名を省略すると同じ`scaling-overview.svg`を上書きするため、3 workloadを連続して
生成するときは必ず別名を指定する。

```bash
python tools/recipe/drone_fleet_performance_plot.py \
  work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/experiment-b.json \
  --x-field process_count --drone-count 32 \
  --output work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/plots/uav-032.svg

python tools/recipe/drone_fleet_performance_plot.py \
  work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/experiment-b.json \
  --x-field process_count --drone-count 64 \
  --output work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/plots/uav-064.svg

python tools/recipe/drone_fleet_performance_plot.py \
  work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/experiment-b.json \
  --x-field process_count --drone-count 128 \
  --output work/recipes/drone-fleet-multi-process-scaling/results/multi-process-scaling/summary/plots/uav-128.svg
```

### 9.2 AVAILABLE: cross-machine / multi-host report generator

Mac/WSL2比較とC matrixを再生成する統合report generatorは次である。

```text
python tools/recipe/drone_fleet_performance_report.py \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  render --experiment <experiment-a|experiment-b|experiment-c> \
  [--include-temporal]
```

入力パスと出力パスはlayout authorityから解決し、任意のdirectoryを暗黙探索しない。
B/Cの完了確認では`--include-temporal`を指定し、performance summaryとは別seriesの
Temporal Validation summaryを同じreportとmanifestへ関連付ける。必要なsummaryが未配置、
未完、またはsuccessでない場合はfail-closedとする。

#### 実行例

Experiment A:

```bash
python tools/recipe/drone_fleet_performance_report.py \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  render --experiment experiment-a
```

Experiment B（Mac/WSL2のperformanceおよびTemporal Validationを収集後）:

```bash
python tools/recipe/drone_fleet_performance_report.py \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  render --experiment experiment-b --include-temporal
```

Experiment C:

```bash
python tools/recipe/drone_fleet_performance_report.py \
  --layout configs/result-layouts/drone-fleet-performance.yaml \
  render --experiment experiment-c --include-temporal
```

既定ではHTML、SVG、PNGを生成する。必要な形式だけに制限するときは、例えば
`--formats svg png`を末尾へ指定する。PNG変換には`rsvg-convert`、ImageMagick、
またはmacOS `sips`のいずれかが必要である。

#### A report

- machineごとのUAV count–RTF/CPU
- MacとWSL2の同一UAV count比較
- successfulかつvalidation/preflightを通過したattemptのmedianとmin/max

#### B report

- 32/64/128 UAVごとのprocess count–RTF
- machineごとの観測ピークprocess数
- RTF 1へ到達する最小process数
- process増加後の性能低下
- Mac/WSL2 overlay

#### C report

- total UAV count–server authoritative RTF
- `srv-01` / `cli-01`の平均CPU
- attempt mean、population standard deviation、min/max（図とderived table）
- extension実行有無と根拠
- incomplete matrixの拒否

#### Temporal Validation report

- Bのprocess endpointごとのlag p95を図示し、median/maximum/acceptance ratioをderived tableへ保存
- Cのhost別lag p95を図示し、lag統計、acceptance ratio、start/end virtual-time差をderived tableへ保存
- performance summaryとは別入力として扱う
- 対応するperformance datasetを参照するが、RTF集計には結合しない

generatorはSVG、HTML、PNGを生成し、同時に`<output-stem>-manifest.json`を保存する。
manifestには次を含める。

- Experiment YAML pathとSHA-256
- result layout pathとSHA-256
- 入力summary pathとSHA-256
- 集計方法
- 出力fileとSHA-256
- tool revision
- derived values

出力directory、stem、formatもresult layoutの`analysis`が正本である。現在のC出力は次になる。

```text
work/recipes/drone-fleet-multi-host-attempt-extension-smoke/results/
multi-host-scaling-preflight/summary/plots/experiment-c-multi-host-scaling.{html,svg,png}
```

## 10. 測定条件とYAMLの対応

| 性能検証上の条件 | YAML field |
| --- | --- |
| UAV数 | `scale.drone_count`, `matrix.drone_count`, `matrix.workloads.*.drone_count` |
| process数 | `scale.process_count`, `matrix.workloads.*.process_count`, `deployment.hosts.*.process_count` |
| attempt数・extension | `matrix.attempts` |
| native/visualization/real-time sync | `runtime.*` |
| Conductor設定 | `runtime.conductor.*`, `measurement.time_coordination.*` |
| scenarioと終了条件 | `scenario.*`, `measurement.stop_conditions.*` |
| preflightとinvalid条件 | `measurement.preflight_*`, `measurement.invalid_conditions.*` |
| samplingとwarmup | `measurement.sampling_interval_sec`, `measurement.warmup_virtual_time_sec` |
| host allocation | `deployment.allocation`, `deployment.hosts.*` |
| network | `deployment.transport`, `deployment.hosts.*.address/connect_to` |
| result series | `results.directory`, `measurement.series` |
| dataset maturity | `measurement.protocol_status` |
| observer modeと周期 | `measurement.mode`, `measurement.temporal_sampling_interval_usec` |

### 10.1 現在宣言されているmatrix

| 系列 | matrix | baseline attempt |
| --- | --- | --- |
| A | UAV `1,2,4,8,16,32,64,128`; process `1` | YAMLの`matrix.attempts` |
| B | UAV `32,64,128`; process `1,2,4,6,8,12,15` | YAMLの`matrix.attempts` |
| B Temporal | UAV `128`; process endpoint `2,15` | 各`1` |
| C | total UAV `64,128,256`; host process `6+12` | `1..3`; trigger時`4..5` |
| C Temporal | total UAV `256`; host process `6+12` | `1` |

この表は読みやすさのためのsnapshotであり、validatorはYAMLを直接検証する。
matrixを変更した場合は、本表、result layout、report mappingを同じreviewで更新する。

## 11. Dataset採用条件

性能値へ採用するattemptは少なくとも次を満たす。

- `status == success`
- validationがPASS
- preflightがPASS
- finiteなRTF、step time、必要なmachine sampleを持つ
- YAMLとresultのconfiguration/attempt identityが一致する
- 同じ比較集合でprotocol statusと測定契約が一致する
- Cでは両hostがpairedでconfig hashが一致する
- required attemptが欠けていない

Temporal Validationは性能系列と別seriesに保持する。observerを有効にした結果のRTFを、
observer無効の性能datasetへ混ぜない。

## 12. 評価資料とのトレーサビリティ

評価資料に掲載する図・表・代表値は次のchainで再現できなければならない。

```text
評価項目
  -> Experiment A/B/C
  -> Experiment YAML + revision/hash
  -> configuration_id + attempt
  -> raw result.json
  -> summary JSON/CSV
  -> <output-stem>-manifest.json
  -> SVG/PNG/HTML
```

### 12.1 PLANNED: report mapping manifest

評価資料の構成が確定した時点で、図表番号を手順書へ直接埋め込まず、次の情報を持つ
version-controlled YAMLを追加する。

```yaml
version: 1
items:
  performance-figure-id:
    experiment: experiment-b
    configurations: declared-in-manifest
    metric: rtf
    statistic: mean
    input_summary: resolved-by-result-layout
    report_artifact: resolved-by-result-layout
```

manifest validatorは、存在しないexperiment/configuration/metric、未完attempt、異なる
protocol status、hash不一致を拒否する。代表値を文書へ転記するときも、元のsummary rowと
集計方法を記録する。

## 13. 完了チェックリスト

### Environment

- [ ] 全machine/hostでrevisionとmachine metadataを保存した
- [ ] Recipe doctorがPASSした
- [ ] Cではwired networkとoutbound接続を確認した

### Measurement

- [ ] `plan`の全configurationがRECORDEDになった
- [ ] required attemptが揃った
- [ ] B/Cに対応するTemporal Validationがcompleteになった
- [ ] invalid/failed resultを採用集合から除外した
- [ ] Launcherとnative processが残っていない

### Collection

- [ ] result layoutどおりにmachine/hostを分離した
- [ ] 転送したartifactのhashとidentityを検証した
- [ ] raw resultを上書きしていない

### Analysis

- [ ] summaryをraw resultから再生成した
- [ ] graphをsummaryから再生成した
- [ ] report manifestへinput/output hashを記録した
- [ ] 評価資料の全数値をconfiguration/attemptまで逆引きできる

## 14. 関連文書

- `tools/recipe/README.md`: operator固有の詳細
- `docs/drone-fleet-multi-host-configuration.md`: multi-host構成と運用契約
- `configs/result-layouts/README.md`: result layoutの責務
- `schemas/recipes/drone-fleet-performance/experiment.yaml`: Experiment schema
- `schemas/result-layouts/drone-fleet-performance.schema.json`: result layout schema
