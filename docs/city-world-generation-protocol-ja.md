# City World生成ジョブ・プロトコル

## 1. 目的

本仕様は、地図で選択したPLATEAU対象範囲を事前診断し、Hakoniwa
EnvsimによるVisual WorldとPhysics Worldの生成を、安全なJSONジョブとして
遠隔実行するための契約を定義する。

制御メッセージには、シェルコマンド、実行ファイル、環境変数、入力パス、
出力パスを含めない。Workerは列挙済みの操作だけを、固定されたローカル実装へ
対応付ける。

## 2. 所有範囲

```text
hakoniwa-business-pack
  - Job request / inspection / message / result schemas
  - Job state machine
  - PDU transport adapter
  - Result artifact transfer and publication policy

hakoniwa-envsim
  - PLATEAU catalog query and normalization
  - Envsim build manifest generation
  - Visual / Physics conversion
  - Dataset Validator and component receipts

hakoniwa-map-viewer
  - Published GLB and generated scene config visualization
  - Drone state visualization for the same world frame
```

## 3. Transport architecture

制御契約はtransport非依存のUTF-8 JSONである。ブラウザとPython Workerでは、
次の共通PDU経路を使用できる。

```text
Browser
  Hakoniwa PDU JavaScript
  WebSocketCommunicationService (wire v2)
           |
           | WebSocket PDU
           v
Python Worker
  Hakoniwa PDU Endpoint (Core-free WebSocket server)
  tools.remote_operation.pdu_transport.PduJsonTransport
  + City World codec
```

Python側では共通`PduJsonTransport`へCity World用`encoder`と`decoder`を注入する。
ブラウザ側では`hakoniwa-pdu-javascript`で同じUTF-8 JSON bytesをPDUとして送受信
する。GLBやMJCF本体は制御PDUへ載せない。

ブラウザとWorkerを直接接続できる構成ではBridgeを必要としない。将来、Workerを
TCP等の別transportへ分離する場合は、JSON契約を変えずにPDU Bridgeを間へ挿入する。

v1の論理PDUは次の1チャネルである。`pdu_size`はブラウザ側定義の上限契約であり、
wire v2のbodyは実際のUTF-8 JSON長で送信する。

```text
robot      : hako_city_world_job
pdu        : message
channel_id : 1
max bytes  : 16384
```

生成物を別ホストへ移す場合は、既存の検証済みZIP artifact channelを使う。
ブラウザ表示時は、公開済みartifact IDから固定HTTP routeでGLBを取得する。
HTTPは生成コマンドの受付には使用せず、生成済み静的artifactの配信に限定できる。

## 4. Machine-readable contracts

```text
schemas/remote-operation/city-world/
  request.schema.json
  inspection.schema.json
  message.schema.json
  result-manifest.schema.json
```

- `request`: ユーザーが選べる中心位置、東西・南北half extent、Building Physics Level、固定profile。
- `inspection`: PLATEAU API応答を正規化した選択範囲診断。
- `message`: command/status envelopeとtype別payload。
- `result-manifest`: ZIPのidentityと固定された論理entry。

## 5. 選択範囲

`half_extent_m`は半径ではない。中心から東西・南北へ伸びる距離である。

```text
north_south = 500m
east_west   = 500m
  -> 1km x 1km bbox
```

v1は各half extentを10m以上1000m以下に制限する。緯度経度からPLATEAU API用
bboxへの変換はWorker側で行い、`west < east`かつ`south < north`を保証する。

## 5.1 Building Physics Level

ブラウザは生成要求ごとに`options.building_physics_level`を0〜3から選択する。既定値は3である。
Workerはこの値をjob固有の`hakoniwa-envsim-build.yaml`に
`mjcf.building_physics_level`として固定する。

| Level | 建物Colliderの判定順序 | Visual / Texture |
|---|---|---|
| 0 | P0 | 最高LOD＋texture |
| 1 | P1 → P0 | 最高LOD＋texture |
| 2 | P2 → P1 → P0 | 最高LOD＋texture |
| 3 | P3 → P2 → P1 → P0 | 最高LOD＋texture |

Levelは建物ごとのP0〜P3再判定と建物collisionだけを変更する。地形、道路、橋、Visual、
textureは変更しない。選択値はrequest hash、job記録、result manifest、生成結果画面へ残す。

## 5.2 Collider削減オプション

ブラウザには階層的なcheck boxを2個置く。両方OFFは`safe`、隣接凸面だけONは
`coplanar-union`、両方ONは`convex-decompose`とする。下段をONにすると上段もONになり、
上段をOFFにすると下段もOFFになる。Workerは選択値を`mjcf.building_collider_reduction`へ
そのまま固定する。

`coplanar-union`はP1〜P3について、同一建物・同一semantic surface種別・同一向きの
同一平面にある、すでに凸なsource polygon Colliderを対象とする。`convex-decompose`はさらに
凹・穴ありsource polygonの三角Colliderを同一source polygon内で再構成する。
いずれも2形状の和集合が単一の穴なし凸polygonになる組だけ段階的に1 Colliderへ統合する。
凸包、頂点snap、隙間補間は行わない。条件不成立時は`safe`のColliderを維持する。
Visual、texture、P0分類、地形、道路、橋は変更しない。実際に使ったモードは
request hash、job記録、result manifest、Envsim Receipt、生成結果画面へ残す。
直方体条件を厳密に満たす建物ColliderはMuJoCo box primitiveへ変換し、生成結果には建物の
`box` / `mesh`内訳も表示する。

## 6. PLATEAU coverage inspection

診断は三層に分ける。

```text
1. National catalog
   GET /datacatalog/plateau-datasets
   -> municipality, year, spec, feature_types

2. Selected bbox
   GET /datacatalog/citygml/r:west,south,east,north
   -> matching files, maxLod, LOD counts, fileSize

3. Generated world
   Envsim Dataset Validator
   -> actually generated Visual / Physics capabilities
```

全国カタログは自治体単位の候補であり、選択bboxの実データ存在を保証しない。
`SELECTION_AVAILABLE`は必ずbbox検索の結果から作る。API生レスポンスはPDUへ
そのまま流さず、`inspection.schema.json`へ正規化する。

`road_markings`のようにCityFurnitureの存在だけでは生成成功を断定できないものは、
事前診断では`generation_status: candidate`または`limited`とし、確定結果は
Dataset Validatorで報告する。

## 7. Commands and statuses

Commands:

- `INSPECT_SELECTION`: 選択範囲のPLATEAU coverageと推定負荷を診断する。
- `GENERATE`: 直前のinspectionに結び付いたrequestを生成する。
- `CANCEL`: Workerが当該jobとして起動した子プロセスだけを停止する。

Status lifecycle:

```text
INSPECT_SELECTION
  -> INSPECTING
  -> SELECTION_AVAILABLE | SELECTION_UNAVAILABLE | FAILED

SELECTION_AVAILABLE
  -> GENERATE
  -> ACCEPTED
  -> DOWNLOADING (sourceごとの進捗通知を反復可能)
  -> GENERATING (component/textureごとの進捗通知を反復可能)
  -> VALIDATING
  -> READY | FAILED | CANCELED
```

`SELECTION_UNAVAILABLE`、`READY`、`FAILED`、`CANCELED`は終端状態である。
`progress`は`phase`と、件数で進捗を表せる場合の`current / total`を保持する。
CityGMLソース取得と建物テクスチャ取得では、ブラウザへ現在件数と総数を表示する。

生成中はブラウザの`生成をキャンセル`から、実行中jobと同じ`job_id`および
`request_sha256`を持つ`CANCEL`を送信できる。WorkerのPDU送受信はメインスレッドだけが
所有し、生成処理は1本のバックグラウンドスレッドで実行する。したがって生成中も
`CANCEL`を受信できる一方、同時に複数のGenerateは実行しない。

キャンセル時は、Workerが当該job用に起動したEnvsimプロセスグループだけへ終了要求を
送り、短い猶予後も残る場合だけ強制停止する。job固有の途中生成物は破棄し、同じjobに
直前の正常成果物があれば復元する。共有CityGML/texture cacheはjobのトランザクション外に
あるため保持する。終了後は`FAILED`ではなく`CANCELED`を返す。実行中でないjob、または
identityが一致しない`CANCEL`は`CANCEL_REJECTED`として拒否する。

## 8. Identity and idempotency

- ブラウザ生成の`job_id`は`都道府県slug-自治体コード-lat小数3桁-lon小数3桁`とする。
  例: `shizuoka-22203-lat35.103-lon138.860`。
- 選択範囲が複数自治体にまたがる場合は、先頭自治体コードの後ろへ`-multi`を付ける。
- 同じ`job_id`へのGenerateは、job固有のbuild、viewer、ZIP、Receiptを置換する。
  共有CityGML/texture cacheはjob外に保持し、置換対象に含めない。
- 置換生成が失敗した場合、Workerは直前の正常なjob成果物を復元する。
- 座標丸めが同じ地域では、範囲、Physics Level等を変えても同じjobを更新する。
  異なる座標セルのjobは別結果として保持し、ブラウザから選択・削除できる。
- `request_sha256`はrequest objectのcanonical JSON SHA-256とする。
- `inspection_sha256`は正規化済みinspection objectのcanonical JSON SHA-256とする。
- `GENERATE`はrequestと`inspection_sha256`を必須とする。
- Workerは`GENERATE`時にもcoverageを再確認し、古いブラウザ状態だけを信用しない。

## 9. Capability semantics

事前診断の各componentは次を分離する。

- `dataset_status`: 選択bboxに対応するPLATEAUファイルがあるか。
- `generation_status`: 現在のprofileで生成候補、制限付き、対象外のいずれか。
- `max_lod`: bbox検索で観測した最大LOD。
- `source_file_count`: 対応ファイル数。
- `reason`: 制限または対象外の理由。

事前診断は生成成功の証明ではない。画面では「Catalog診断」と「生成結果」を
別の表示にする。

## 10. Workload guard

Workerはbbox検索結果から以下を計算する。

- matching municipality count
- source file count
- estimated download bytes
- component別max LOD

対象ファイルがない場合は`SELECTION_UNAVAILABLE`とする。上限超過やPLATEAU APIの
広域検索エラーは、範囲を狭めるための構造化errorとして返す。実行ポリシー上の
最大download bytesはWorker設定が所有し、ブラウザ要求から変更できない。

## 11. Artifact contract

`READY`は`result-manifest.schema.json`に従う。ZIP内の主要entryは固定する。

```text
visual/city-world.glb
physics/city-world.xml
validation/dataset-validation.json
receipt/city-world-receipt.json
```

任意の出力パスをwire protocolで指定しない。Workerは
`work/city-world-jobs/<job-id>/`等の管理領域をローカルポリシーから解決する。

## 12. v1 non-goals

- 複数jobの同時実行
- 任意Envsim manifestの遠隔投入
- 任意コマンド実行
- GLB/MJCF本体のcontrol PDU転送
- 生成中GLBのMap Viewer hot reload
- PLATEAU coverage polygonによる地図全体のmask表示

Map Viewer連携は、`READY`後に生成済みGLBとscene configを読み直すところから開始する。

## 13. Browser inspection and generation

ブラウザから公式PLATEAU APIを使う範囲診断とCity World生成を実通信で確認できる。
Capability診断ではCityGML本体をダウンロードせず、`plateau-datasets`とbbox別catalog
だけを読む。Building、Terrain、Road、Road markings、Bridgeのbbox別catalogは、公開APIへの
負荷を固定上限に抑えた5並列で取得し、広域選択時に通信待ちを直列加算しない。
BridgeはAPI検索だけ2次メッシュを使うが、返却された各ファイルの3次メッシュコードを
選択範囲の3次メッシュ集合で再フィルタする。これにより、同じ2次メッシュ内にある
選択範囲外の橋梁LODや自治体をCapabilityへ混入させない。
`Generate`を明示実行した場合にだけ、WorkerがCityGMLとRecipe依存を取得する。

Terminal 1（PDU Worker）:

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.worker \
  --listen-address 127.0.0.1 \
  --port 54210
```

Workerは、PLATEAU catalogの推定取得量がデフォルトの8 GiBを超えるGenerateを
開始前に拒否する。運用環境のディスク容量やネットワーク条件に合わせて、
`--max-download-gib <GiB>`でこの安全上限を明示的に変更できる。選択範囲自体は
request schemaの上限（各方向のhalf extent 1000 m）でも制限される。

Terminal 2（限定static server）:

```bash
python3 -m tools.remote_operation.city_world.web_smoke --port 8008
```

通常利用では、上記2プロセスを別Terminalで起動せず、Coreless City World Launcherを使う。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher start \
  --parallel-workers 4 \
  --dem-parallel-workers 2 \
  --terrain-spacing-m 2 \
  --open-browser
```

このLauncherは既存Hakoniwa Launcherの`activate-only`モードを利用する。WorkerとWebを
どちらも`before_start` assetとして起動し、`hako-cmd start/stop/reset`は呼び出さない。
状態確認と停止は次のとおり。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher status

python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher stop
```

生成されるLauncher設定、session、個別ログは
`work/remote-operation/city-world-launcher/`に置く。CityGML cacheと生成jobは従来どおり
`work/remote-operation/city-world-worker/`に置き、Launcherを停止しても削除しない。
`--open-browser`を省略した場合はブラウザを自動起動せず、表示されたURLを人間が開く。

### 13.1 並列worker数と地形解像度の設定

ブラウザ版では、並列数をPythonソースへ直接記述せず、Launcher起動時の
`--parallel-workers <1..16>`と`--dem-parallel-workers <1..4>`で指定する。
既定値はそれぞれ`4`と`2`である。DEMは1 processごとにCityGMLを読み、抽出結果を保持するため、
メモリ暴走を防ぐ目的で上限を4に固定する。これらの値はLauncherからWorkerへ渡され、各jobの
次のファイルへ記録される。

```text
work/remote-operation/city-world-worker/jobs/<job_id>/hakoniwa-envsim-build.yaml
```

生成時には、上記YAMLの`city_world.parallel_workers`と
`city_world.dem_parallel_workers`としてEnvsimへ渡る。`job.json`の
`generation_policy`にも同じ値を残す。値を変更する場合は、
稼働中のLauncherを停止してから新しい値で起動し直す。

```bash
python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher stop

python3 tools/workspace.py run -- \
  python3 -m tools.remote_operation.city_world.launcher start \
  --parallel-workers 6 \
  --dem-parallel-workers 4 \
  --terrain-spacing-m auto \
  --open-browser
```

`parallel_workers`が上限として使われる工程は、PLATEAU source・LOD2 textureの取得、建物GML抽出と、出力先が独立した建物Visual、
建物Physics、道路、路面標示、橋梁componentの生成である。ComposerとDataset Validatorは
依存componentの完了後に直列実行する。建物GML抽出は巨大XMLをprocessごとに保持するため、
`parallel_workers`が5以上でも最大4processへ制限する。`dem_parallel_workers`はDEM source抽出だけに使われ、
実際のprocess数はこの値と対象DEM source数の小さい方になる。

値は次の順序で決める。

1. まず既定値`4`で、CPU使用率、メモリ使用量、処理時間を確認する。
2. source取得または独立component生成が律速し、CPUとメモリに余裕がある場合は`6`、次に`8`を試す。
3. CPU使用率の飽和、メモリ圧迫、swap、ディスクI/O待ちが増えた場合は一段階戻す。
4. `8`を超える値は、同じ入力範囲で実測して短縮を確認できた場合だけ使用する。

DEMは既定値`2`から開始し、複数DEM sourceの抽出が律速し、CPUとメモリに余裕がある場合だけ
`4`を試す。ラスタライズと小欠損補間はこの値では並列化されないため、DEM source抽出完了後の
待ち時間には効果がない。

並列に実行できるcomponent数とsource数以上のworkerは待機するため、値を増やせば必ず速くなる
わけではない。再現性のある比較では、同じ選択範囲、Physics Level、cache状態で所要時間を比較する。

EnvsimをブラウザWorker経由ではなく直接実行する場合は、実行対象の
`hakoniwa-build.yaml`または`hakoniwa-envsim-build.yaml`に同じ設定を書く。

```yaml
city_world:
  parallel_workers: 6
  dem_parallel_workers: 4
```

`--terrain-spacing-m`は`2`、`5`、`10`、`auto`から選ぶ。既定値`2`は従来動作を維持する。
`auto`は選択範囲から各候補のhfield sample数を推定し、120,000 sample以下になる最も細かい
間隔を選ぶ。目安は次のとおりである。

| 正方形の範囲 | `auto`の選択 |
|---|---:|
| 200 m四方 | 2 m |
| 1 km四方 | 5 m |
| 2 km四方 | 10 m |

選択結果はjob固有YAMLの`city_world.terrain_spacing_m`へ数値として書き込み、`job.json`には
requested値、effective値、推定sample数、autoのsample上限を記録する。したがって、同じjobを
後から検証しても、自動選択された実効解像度を確認できる。Envsimを直接実行する場合、`auto`は
Business Pack Workerの方針なので、YAMLには決定後の数値を書く。

ブラウザで`http://127.0.0.1:8008/`を開き、次の順に実行する。

Visual GLBは`embedded-if-available`で生成する。選択範囲のLOD2 Appearanceと
参照画像が利用できる面はテクスチャをGLBへ埋め込み、利用できない面だけを
フラット表示へフォールバックする。複数都市を軽量に一括検証するRecipeの
`texture_mode: flat`とは用途を分離している。

1. 地図で範囲を選択する。
2. `Capabilityを診断`を押す。
3. `生成候補あり`の場合に有効になる`City Worldを生成`を押す。

```text
INSPECT_SELECTION
INSPECTING
SELECTION_AVAILABLE | SELECTION_UNAVAILABLE | FAILED

GENERATE
ACCEPTED
DOWNLOADING
GENERATING
VALIDATING
READY | FAILED | CANCELED
```

生成中は、PLATEAUソース、建物形状、DEM source抽出、DEM小欠損補間、建物Physics、建物Visual、建物テクスチャ、
道路、LOD3路面標示、橋梁、City World統合、検証・ZIP作成を別phaseとして表示する。
建物テクスチャは選択範囲で実際に参照される画像を先に確定し、`current / total`を通知する。
source取得とDEM source抽出は複数workerで処理する。terrainとworld-frame確定後は、出力先が
独立した建物Visual、建物Physics、道路、路面標示、橋梁componentを並列生成し、Composerと
Dataset Validatorだけを依存component完了後に直列実行する。並列phaseの通知順にかかわらず、
画面の進捗率は後退させない。

外部toolが長時間標準出力を出さない場合も、Workerは15秒ごとに現在phaseのheartbeat statusを
送る。ブラウザのstatus待機期限は「生成全体の制限時間」ではなく、Workerから一定時間まったく
応答がない通信異常を検出するための期限である。

画面ではLeafletの地図クリックまたは中心マーカーのドラッグで位置を選ぶ。選択矩形
そのものをドラッグすると中心を移動でき、四隅のハンドルをドラッグすると東西・南北
half extentを変更できる。緯度・経度またはhalf extentを入力欄で確定した場合も、
その選択範囲が地図へ収まるよう自動的に移動する。診断後はBuilding、Terrain、Road、Road markings、Bridgeの
availability、最大LOD、対象ファイル数を個別表示する。診断に利用したPLATEAU 3次メッシュは
緑の破線で地図へ重ね、メッシュコードも表示する。青い矩形はユーザーの切り出し範囲であり、
メッシュ境界をまたいでもよい。

PLATEAU catalogのfeature別404は通信障害ではなく、その区画に当該データが存在しない
Capability結果として表示する。通信/API障害の生メッセージは画面本文へ露出させず、
通信ログへ記録する。

`web_smoke`はCity World画面と`hakoniwa-pdu-javascript/src`だけを配信し、Workspace
全体をHTTP公開しない。生成成功時には画面へ`Generate成功`とartifact名を表示する。
生成物は次に配置される。

```text
work/remote-operation/city-world-worker/jobs/<job-id>/
  build/world/
    city-world.glb
    city-world.xml
    dataset-validation.json
    city-world-receipt.json
  artifacts/
    city-world-<job-id>.zip
    result-manifest.json
  viewer/
    city-world.glb
    city-world-colliders.glb
    city-world-colliders-receipt.json
  generation.log

work/remote-operation/city-world-worker/cache/plateau-citygml/
  objects/
    <source-identity-hash>/
      <citygml-file>
      <citygml-file>.cache.json
      textures/
        <texture-url-sha256>.<ext>
```

CityGMLの元ファイルはjobとは別のWorker共有キャッシュへ保存する。source URLとCatalog
記載サイズをcache identityとし、別の範囲でも同じidentityを選択した場合、保存済みobjectの
実サイズとSHA-256を検証してからjob固有の
`build/source/`へmaterializeする。同一filesystemではhard linkを使い、利用できない環境だけ
file copyへフォールバックする。範囲がメッシュ境界を越えた場合は不足objectだけを取得する。
LOD2建物テクスチャもsource identity配下の共有cacheへ保存し、別jobで同じURLを参照した
場合は再取得せずにGLBへ埋め込む。
範囲切り出しとquery-centered local ENU原点は変わるため、変換処理と生成物はjobごとに再作成する。

画面の生成結果プルダウンには、検証済み`result-manifest.json`を持つjobだけを更新時刻の
新しい順に表示する。選択すると生成時の中心・half extentを入力欄へ戻し、その範囲を
オレンジの破線で地図へ重ねる。選択中のjobについて、Workspaceからの相対パスを画面へ表示し、
共有cacheの相対パス、object数、総容量も別に表示する。
同じ欄に生成時のBuilding Physics Level、Collider削減モード、Physics Worldの総Collider geom数、
terrain/buildings/bridges等のcomponent別内訳、P0〜P3別の建物geom数を表示する。
`Download ZIP`を押した場合だけZIP本体を取得し、一覧表示や3D表示の時点ではZIPを転送しない。
`生成結果を削除`は、確認後にサーバー上の選択jobディレクトリを丸ごと削除する。
ZIP、GLB、MJCF、中間生成物も削除対象となるが、job外の共有CityGML cacheは保持する。

`3D Viewer`を押すと、選択した`viewer/city-world.glb`をThree.jsで地図の下へ表示する。
3D表示は`Visual`と`Collider`のcheck boxで切り替え、両方OFFは禁止する。両方ONで重ね合わせ、
片方だけONで単独表示する。Collider GLBは
MJCFのbox、inline mesh、terrain hfieldを同じworld-frameで変換したデバッグ表示であり、
MuJoCoがcollisionの正本であることは変わらない。Colliderは細い緑色の半透明wireframeとし、
depth testを有効にしてVisualの手前へ不要な裏側の線を透過表示しない。
Viewerは操作時だけ再描画し、待機中に連続描画しない。`web_smoke`がHTTP公開する生成物は、
検証済みjobのVisual GLB、Collider GLB、ZIPに限定する。任意のWorkspaceパス、manifest、
MJCF、ログは公開しない。
Workerのruntime場所を変更した場合は、static serverにも同じ場所を指定する。

```bash
python3 -m tools.remote_operation.city_world.web_smoke \
  --port 8008 \
  --worker-runtime-dir work/remote-operation/city-world-worker
```

Workerは停止するまで複数回の診断・生成commandを受け付ける。`--once`は自動テスト用で、
最初のcommand応答後に正常終了する。その終了時にnative Endpointが
`Accept error: Operation canceled`を表示する場合があるが、応答送信後であれば異常ではない。
