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

- `request`: ユーザーが選べる中心位置、東西・南北half extent、固定profile。
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
  -> DOWNLOADING (cached sourceなら省略可能)
  -> GENERATING
  -> VALIDATING
  -> READY | FAILED | CANCELED
```

`SELECTION_UNAVAILABLE`、`READY`、`FAILED`、`CANCELED`は終端状態である。

## 8. Identity and idempotency

- `job_id + request_sha256`が同じ再送は、同一jobの現在状態として扱う。
- 同じ`job_id`に異なる`request_sha256`を指定した場合は拒否する。
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
だけを読む。`Generate`を明示実行した場合にだけ、WorkerがCityGMLとRecipe依存を取得する。

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
READY | FAILED
```

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
```

CityGMLの元ファイルはjobとは別のWorker共有キャッシュへ保存する。source URLとCatalog
記載サイズをcache identityとし、別の範囲でも同じidentityを選択した場合、保存済みobjectの
実サイズとSHA-256を検証してからjob固有の
`build/source/`へmaterializeする。同一filesystemではhard linkを使い、利用できない環境だけ
file copyへフォールバックする。範囲がメッシュ境界を越えた場合は不足objectだけを取得する。
範囲切り出しとquery-centered local ENU原点は変わるため、変換処理と生成物はjobごとに再作成する。

画面の生成結果プルダウンには、検証済み`result-manifest.json`を持つjobだけを更新時刻の
新しい順に表示する。選択すると生成時の中心・half extentを入力欄へ戻し、その範囲を
オレンジの破線で地図へ重ねる。選択中のjobについて、Workspaceからの相対パスを画面へ表示し、
共有cacheの相対パス、object数、総容量も別に表示する。
`Download ZIP`を押した場合だけZIP本体を取得し、一覧表示や3D表示の時点ではZIPを転送しない。
`生成結果を削除`は確認後に選択jobだけを削除し、job外の共有CityGML cacheは保持する。

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
