# 箱庭ランタイム入門

[English](hakoniwa-runtime-primer.md)

このドキュメントでは、AIや人が箱庭Recipeを書く前に理解しておく必要があるランタイム上の前提を説明します。

Catalogエントリは部品を説明します。Recipeはシステム構成を説明します。このPrimerでは、それらの構成を成立させるためのランタイム上のルールを説明します。

## 基本となるメンタルモデル

箱庭は単なるライブラリ群ではありません。実行中の箱庭システムは、共通のシミュレーションランタイムに参加する複数のアセットから構成されます。

一般的なランタイムモデルは次の通りです。

```text
Hakoniwa core runtime
  - simulation lifecycle
  - simulation time
  - shared-memory PDU data space
  - asset coordination

Assets
  - simulator
  - controller
  - visualizer
  - bridge
  - service
  - external tool
```

Recipeでは、どのプロセスが箱庭アセットで、どのプロセスが外部クライアントなのか、そしてそれらがどのようにデータを交換するのかを明確にする必要があります。

## Core-Pro と Core-Cpp の役割

`hakoniwa-core-cpp` は低レベルのシミュレーションハブです。共有メモリのマスターデータ、PDUバッファ、アセット登録、シミュレーションイベント制御、ワールド時刻APIを提供します。

`hakoniwa-core-pro` は、その基盤をユーザー向けにパッケージ化し、拡張したものです。インストール済みのヘッダ／ライブラリ、`hakopy`、`hako-cmd`、Conductorサポート、アセットAPI、データ受信イベント、PDUベースのサービスを提供します。

`hakoniwa-pdu-python` はPythonパッケージ `hakoniwa-pdu` としてインストールされます。Python向けPDUユーティリティと、箱庭Launcherのエントリポイントを提供します。

```bash
python -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
```

ユーザー向けRecipeでは、次のように扱ってください。

- `hakoniwa-core-pro`: ユーザーがインストールし、依存するコンポーネント。
- `hakoniwa-core-cpp`: ランタイム協調の仕組みを説明する、同梱されたコア機構。
- `hakoniwa-pdu-python` / `hakoniwa-pdu`: PDU変換／通信APIとLauncherアプリケーションを提供するPythonパッケージ。

Recipeがコア開発、共有メモリのデバッグ、低レベルアセットAPIを扱うものでない限り、ユーザーに `hakoniwa-core-cpp` の内部実装を前提とした理解を求めないでください。

## コアランタイムの要件

デモが箱庭PDU共有メモリを使用する場合、箱庭コアランタイムが利用可能でなければなりません。`hakopy` をimportする、あるいは共有メモリPDU APIを使うコントローラや可視化ツールは単独では動作せず、初期化済みの箱庭ランタイムと互換性のあるPDU定義を必要とします。

一般的な要件には次のものがあります。

- インストール済みの箱庭コアライブラリとツール。
- 共有メモリへのアクセス権限。
- 生成済み、またはパッケージ済みのPDU定義。
- Recipeで明示的に別バージョンが指定されていない限り、箱庭PythonワークフローではPython 3.12。
- `hakopy` を使う場合、Python 3.12環境とネイティブランタイム環境が一致していること。
- シミュレーションライフサイクルの所有者。通常は `hako-cmd` またはLauncherから制御します。

コアランタイムとPDU設定の経路が確認できるまでは、SHM/PDUデモを実行可能なものとして提示しないでください。

Linux/macOSでは、一般的なインストール先は `/usr/local/hakoniwa` です。インストールされるランタイム成果物には、次のようなものがあります。

- `/usr/local/hakoniwa/bin` 配下の `hako-cmd`。
- `/usr/local/hakoniwa/lib` 配下のライブラリ。
- `/usr/local/hakoniwa/include/hakoniwa` 配下のC/C++ヘッダ。
- `/usr/local/hakoniwa/share/hakoniwa/offset` 配下のPDUオフセットファイル。
- `/etc/hakoniwa/cpp_core_config.json` のコア設定。
- `/var/lib/hakoniwa/mmap` 配下のmmapデータ。
- デモで使用するPython環境内の `hakopy`。

`hakopy` は `hakoniwa-core-pro` のインストールによって利用可能になります。Pythonの `site-packages` にインストールされるため、Recipeで使用するPython 3.12インタプリタは、その `hakopy` モジュールをimportできる同じ環境でなければなりません。

`hakoniwa-pdu` は `hakoniwa-pdu-python` プロジェクトから `pip install hakoniwa-pdu` によって導入されます。これも現在有効なPython 3.12環境にインストールされます。

あるPythonでは `hakopy` をimportできる一方、別のPython環境に `hakoniwa-pdu` がインストールされている場合、そのデモを実行可能な状態とは見なせません。Recipeには使用するPythonコマンドまたは環境を記録し、同一のPython 3.12インタプリタで両方をimportできることを確認してください。事前確認には次のコマンドが有効です。

```bash
python3.12 -c "import hakopy; import hakoniwa_pdu"
python3.12 -m pip show hakoniwa-pdu
```

## アセット

アセットとは、箱庭の規約に従って管理されるランタイム参加者です。ネイティブシミュレータ、コントローラ、可視化ツール、ブリッジ、サービスなどが該当します。

アセットには一般に次の情報があります。

- アセット名。
- プロセス実行コマンド。
- シミュレーション開始に対する起動タイミング。
- publishまたはsubscribeするPDUチャネル。
- シミュレーション開始／停止時のライフサイクル動作。
- ログや観測可能な出力。

有用なプロセスのすべてが厳密な箱庭アセットである必要はありません。PDUデータの読み書き、Bridge経由の接続、Service APIの呼び出しなどにより、外部クライアントとして緩やかに参加できるツールもあります。Recipeでは、この違いを明確にする必要があります。

コアのサンプルは、基本的なパターンを示しています。

- `examples/hello_world`: 1つのアセットが登録され、`hako-cmd start` を待ち、コールバックを実行し、その後 `stop` と `reset` に応答します。
- `examples/pdu_communication`: 2つのアセットがプラントとコントローラとして動作し、設定ファイルを通してPDUチャネルを共有します。シミュレーションのstart/stop/resetには別のコマンドプロセスが必要です。
- `examples/service`: アセットがPDUベースのrequest/responseサービスを公開し、呼び出します。
- `examples/external/topic` と `examples/external/service`: Pythonプロセスが、通常の登録済みシミュレーションアセットにならずに、SHM topicまたはservice APIを通して参加できます。

## PDU

PDUはProtocol Data Unitの略です。箱庭Recipeにおいて、PDUはコンポーネント間で交換される型付きデータ契約です。

例:

- ロボットの姿勢。
- 車輪やゲームコントローラのコマンド。
- LiDARスキャン。
- ドローンの位置と姿勢。
- カメラやセンサの状態。
- Serviceのrequest/response payload。

PDU契約はtopic名だけではありません。Recipeでは次の情報を明確にする必要があります。

- PDU名またはチャネル。
- PDU型。
- producer。
- consumer。
- SHM、Endpoint、Bridge、WebSocket、RPCなどの転送経路。
- 双方が使用するPDU定義／設定ファイル。

PDU型、名前、設定ファイルのパスが不明な場合、その接続は完全には定義されていません。

## PDU定義ファイル

箱庭のPDU設定には、通常2つの異なる層があります。これらを1つの概念として扱わないでください。

```text
PDU schemas and generated bindings
  -> concrete PDU type layouts, sizes, offsets, converters

pdutypes.json
  -> the PDU channels available in one PDU type set

pdudef.json or pdu_def.json
  -> which PDU type set is assigned to which robot or asset name

runtime participants
  -> simulator, controller, bridge, visualizer, or external client using the
     same PDU definition/config
```

`pdutypes.json` は、1つのPDU type setの中身を定義します。通常、次のようなエントリを列挙します。

- `channel_id`: そのtype set内での数値チャネルID。
- `pdu_size`: バイナリpayloadのサイズ。
- `name`: `laser_scan` や `hako_cmd_game` などの意味的なPDU名。
- `type`: `sensor_msgs/LaserScan` などのmessage type。

形式例:

```json
[
  {
    "channel_id": 3,
    "pdu_size": 8192,
    "name": "laser_scan",
    "type": "sensor_msgs/LaserScan"
  }
]
```

`pdudef.json` または `pdu_def.json` は、それらのPDU type setをランタイム上の名前に割り当てます。compact formatでは、一般に `paths[].id` を `pdutypes.json` ファイルに対応づけ、その後、各 `robots[].name` をそれらのIDのいずれかに対応づけます。

```json
{
  "paths": [
    { "id": "tb3-endpoint", "path": "pdutypes.json" }
  ],
  "robots": [
    { "name": "TB3", "pdutypes_id": "tb3-endpoint" }
  ]
}
```

要するに、次のような役割分担です。

- `pdutypes`: 1つのsetに、どのチャネルとバイナリレイアウトが存在するか。
- `pdudef`: そのsetを、robot名またはasset名によってシステム上のどこに配置するか。
- generated bindings: PDU型に対応する言語固有のstruct／classとconverter。
- offset files: converterやruntime toolが使用するバイナリレイアウトのmetadata。
- endpoint/bridge/viewer configs: 互換性のあるPDU名、型、サイズ、割り当てを参照しなければならないランタイム配線設定。

既存のデモでは、RecipeがPDU空間を明示的に変更しない限り、提供されている `pdutypes` と `pdudef` ファイルを再利用してください。新しいシステムでは、両方の層を設計する必要があります。

1. producerとconsumerに必要なPDUチャネルと型を定義する。
2. 対応するbinding、size、offsetを生成または選択する。
3. 生成したPDU type setを、ランタイムで使用するrobot名またはasset名に割り当てる。
4. すべてのsimulator、controller、bridge、visualizer、external clientで、同じ互換性のある定義ファイルを使用する。

AIがweather、wind、sensor、controllerなどの新しいアセットを提案する場合は、新しいPDUチャネルと、そのチャネルをランタイムPDU定義上のどこに配置するかの両方を明確にしなければなりません。そうでなければ、そのRecipeは概念的な案にとどまり、完全な箱庭システム構成にはなりません。

## PDU Registry

`hakoniwa-pdu-registry` は、箱庭コンポーネント向けPDU schemaと生成済みbindingの情報源です。多くのRecipeにおけるデータモデルの基準となります。

Recipeで次の問いに答える必要がある場合に使用してください。

- どのPDU型が存在するか。
- どの言語向け生成bindingが必要か。
- Python、JavaScript、C++、Godot、Foxgloveがそのデータを読み取れるか。
- simulator、controller、bridge、visualizer間で共有すべきcompact PDU定義やEndpoint設定はどれか。

双方が「PDU」に言及しているという理由だけで、2つのコンポーネントに互換性があると判断しないでください。名前、型、サイズ、生成binding、設定ファイルが一致している必要があります。

## シミュレーション時刻

箱庭では、プロセスの起動とシミュレーション時刻の進行を分離して扱います。

シミュレータプロセスを起動しただけでは、シミュレーション時刻が進行しているとは限りません。多くのデモでは次のような流れになります。

```text
1. Conductorを内包する構成なら、そのownerプロセスを起動する
2. simulator系アセットと、事前登録が必要なcontrollerアセットを起動する
3. 必要なアセットが登録され、WAIT START相当の状態になるまで待つ
4. hako-cmd startを実行する
5. start後に動かすcontroller、visualizer、bridge clientを起動する
6. 状態変化を観測する
7. 環境が対応していればhako-cmd stop、またはLauncher／プロセス方針でassetを終了する
```

`hako-cmd start` と `hako-cmd stop` はシミュレーションのライフサイクルを制御します。OSプロセスの起動や強制終了とは別のものです。

Conductor起動を内包するアセットプログラムでは、その実行ファイルの起動が箱庭ランタイムドメインの作成とplant asset登録を担うことがあります。この場合、手動デモの正しい順序は次のようになります。

```text
terminal 1: Conductor ownerであるplant asset programを起動する
            asset登録、PDU channel作成、WAIT STARTを確認する
terminal 2: 時刻開始前に参加すべきcontroller/sender assetを起動する
            登録完了とWAIT STARTを確認する
terminal 3: hako-cmd start
```

`hako-cmd start` をデモの最初のcommandとして扱わないでください。これは登録済みで待機中のアセットを、実行中のシミュレーション時刻へ遷移させる操作です。controllerが登録済みアセットではなく通常の外部クライアントである場合、Recipeではそれを `hako-cmd start` の前に起動するのか後に起動するのかを明示してください。

したがって、ランタイム検証ではシミュレーション開始後の動作を確認する必要があります。

- シミュレーションstep／時刻が進む。
- PDU値が変化する。
- ロボット／ドローンの姿勢が変化する。
- コントローラのコマンドが消費される。
- センサが意味のあるデータを生成する。

プロセスが起動しただけでは、部分的な証拠にしかなりません。

コールバック型アセットでは、ユーザーから見える兆候は次のようになることがあります。

```text
WAIT START
hako-cmd start
WAIT RUNNING
PDU DATA CREATED
on_initialize / on_simulation_step callbacks
hako-cmd stop
hako-cmd reset
on_reset callback
```

Polling型や外部クライアントでは、代わりにSHM初期化の成功、Service開始、PDU publish/subscribe callback、Service responseなどが兆候になります。Recipeでは、選択した実行形態に応じた期待される兆候を記載してください。

## Conductor

Conductorは箱庭のシミュレーション時刻とライフサイクル動作を協調させます。1つの構成済みシミュレーションでは、コンポーネントのドキュメントやRecipeに明示的な記載がない限り、Conductorは単一の所有者として扱ってください。

重要なルール:

- 1つの構成済みデモでは、通常Conductorの所有者は1つにする。
- 同じSHM／ランタイムドメインに対して、複数の独立したConductor所有者を起動しない。
- マルチプロセスデモでは、どのプロセスがConductor起動を担当するかを決める。
- 他のシミュレータプロセスでは、Conductor起動を無効にするか、ドキュメントで定義された非ownerモードを使用する。

このルールは、マルチロボットやmirror-bodyデモで特に重要です。

APIによっては `conductor_start()` / `conductor_stop()` のような明示的なConductor操作や、内部でConductorを起動するhelper wrapperを提供するものがあります。それでもsingletonルールは変わりません。Recipeでは、それらのAPIを呼び出してよいプロセスを明確にする必要があります。

## 標準的なプロセス役割

起動順序とデータフローを考える際には、次の役割を使用してください。

- `simulator`: 物理演算またはドメインシミュレーションを担当し、通常は状態をpublishします。
- `controller`: command PDUまたはService callを送信します。
- `visualizer`: 状態やセンサデータを表示し、物理演算を所有すべきではありません。
- `bridge`: SHMからWebSocketなど、異なるtransport間でPDUデータを移送します。
- `service`: commandまたはRPC操作を公開します。
- `asset_generator`: デモ開始前にランタイム成果物を生成します。
- `external_client`: メインのシミュレーションランタイムにはならず、PDU、Bridge、RPC、APIを通して参加します。

これらの役割は入れ替え可能ではありません。たとえば、ブラウザviewerはphysics simulatorの代わりにはなりません。また、upstream producerが動作していなければ、Bridgeが存在するだけでは状態が変化していることを証明できません。

## 登録済みアセットと外部クライアント

箱庭は、登録済みアセットと、より緩やかに参加する外部クライアントの両方をサポートします。

登録済みアセットは一般に次のように動作します。

- アセット登録APIを呼び出す。
- シミュレーションライフサイクルイベントを受信する。
- start/stop/resetを待つ。
- ワールド時刻の進行に参加する。
- アセット設定から論理PDUチャネルを作成する場合がある。

外部クライアントは一般に次のように動作します。

- 既存のSHM/PDU Service設定に接続して初期化する。
- `read_pdu_for_external`、`write_pdu_for_external`、SHM topic publisher/subscriber、SHM Service client/server helperなどのAPIでPDUデータを読み書きする。
- 通常のアセットライフサイクルcallbackを受け取らない場合がある。
- 共有メモリドメインを初期化済みのランタイム所有者を必要とする。

物理演算やコアライフサイクルを所有すべきでないツール、モニタ、簡易コントローラ、Service clientには外部クライアントを使用してください。実際のsimulator、PDU設定、Conductor ownerの定義を省略するための近道としてexternal modeを使わないでください。

## Launcher

複数のプロセスを特定の順序で起動する必要がある箱庭デモでは、Launcherの利用を優先してください。Launcher設定はランタイム成果物です。

一般的に使用される箱庭Launcherは、`hakoniwa-pdu-python` リポジトリ由来の `hakoniwa-pdu` Pythonパッケージによって提供されます。次のように起動します。

```bash
python -m hakoniwa_pdu.apps.launcher.hako_launcher path/to/launch.json
```

`hakoniwa-pdu` Launcherのモデルでは、次の要素を使用します。

- `name`、`command`、`args`、`cwd`、`stdout`、`stderr`、`env`、`activation_timing`、`depends_on`、`delay_sec`、`start_grace_sec` を持つ `assets[]`。
- 共通のcwd、logs、env操作、delay、grace periodを定義する `defaults`。
- `set`、`prepend`、`append`、`unset` などの環境変数merge操作。
- `activate -> hako-cmd start -> watch` を実行する `immediate` mode。
- `activate`、`start`、`stop`、`reset`、`terminate`、`status` などのcommandを受け付ける `serve` mode。
- 1つのアセットが予期せず終了した場合に全アセットを停止するwatch動作。

Launcherは通常、起動後に終了するsetup commandではなく、長時間動作するlifecycle managerです。`immediate` modeでは、起動したアセットを監視しているため、`hako-cmd start` 後もforegroundに残ることがあります。これは正常なランタイム動作として扱ってください。AIやscriptが後続のcommandを実行する必要がある場合は、Launcher sessionをbackgroundで維持する、controller用に別terminal/sessionを使う、または必要に応じて `serve` modeを使用する必要があります。

Launcherが終了しないという理由だけで、Launcherベースのデモが失敗したと判断しないでください。また、log fileが空であるという理由だけでアセットが失敗したと判断しないでください。`python -m http.server` のように、ブラウザからrequestが来るまでstdoutへ何も出力しないアセットもあります。次のようなactive readiness checkを優先してください。

- Launcher出力が `hako-cmd start exited with 0` に到達している。
- 想定したアセットがまだ実行中である。
- `curl -I http://127.0.0.1:8000/` のように、想定したsocketが応答する。
- 想定したBridge endpointがbrowserまたはclient connectionを受け付ける。
- downstreamのPDU、Service、visual-state logでデータ変化が確認できる。

Launcherの起動タイミングは `activation_timing` で表現します。

```text
before_start assets
  - simulator
  - runtime services that must initialize PDU data

hako-cmd start
  - simulation time starts

after_start assets
  - controllers
  - visualizers
  - bridge clients
  - scripted demo drivers
```

アセット間の起動順序制約には `depends_on` を、実際の起動タイミング調整には `delay_sec` / `start_grace_sec` を使用してください。別世代のツールからLauncher fieldを推測して追加しないでください。RecipeがLauncher動作に依存する場合は、インストールされている `hakoniwa-pdu` のversionとschemaを確認してください。

Launcherによっては、中間成果物として具体的なlaunch fileを生成するものがあります。Recipeで明示的にcustomize pointとして定義されていない限り、デモ実行中に生成済みlaunch fileを直接編集しないでください。再生成によって編集内容が失われる可能性があります。commandを変更する必要がある場合は、生成元のscript、環境変数、またはRecipe parameterを確認し、それを修正として扱う前に、新しいissueまたはRecipe更新として記録してください。

Recipeを書く際には、次の内容を記録してください。

- Launcher file path。
- Launcher providerと起動command。
- アセット名。
- アセットcommand。
- 起動タイミング。
- 環境変数。
- log file。
- 想定する終了動作。
- 中断された場合のcleanup動作。

Launcherが存在しない、あるいはRecipeが意図的にmanual runbookを記述している場合を除き、LauncherベースのRecipeを場当たり的なterminal command一覧に置き換えないでください。

## 外部・疎結合インテグレーション

有用な参加者の中には、厳密な箱庭アセットではないものもあります。integration boundaryが明確であれば、そうした参加者もRecipeの一部として扱えます。

例:

- `hakopy` を通してSHM PDUを読み書きするPython script。
- WebSocket Bridge経由で接続するbrowser viewer。
- `hakoniwa-pdu-javascript` を通して接続するNode.js monitor。
- 箱庭Serviceを呼び出すRPC client。
- sensor PDU streamをsubscribeするplotting tool。

外部参加者については、次の点を明確にしてください。

- どのように接続するか。
- コアランタイムが事前に存在している必要があるか。
- シミュレーション開始前から起動できるか、開始後にのみ起動できるか。
- simulatorまたはBridgeが存在しない場合にどうなるか。

外部連携は有効な構成ですが、実際のproducer、runtime、PDU設定、観測可能なvalidationが必要であることに変わりはありません。

## Recipe起動チェックリスト

実行可能な手順を書く前に、次の問いに答えてください。

- コアシミュレーションランタイムを所有するコンポーネントはどれか。
- Conductorを起動または所有するプロセスはどれか。
- `hako-cmd start` より前に存在していなければならないアセットはどれか。
- どの実行ファイルがアセット登録を行い、どのログでstart待ち状態を確認するか。
- シミュレーション時刻開始後に起動するアセットはどれか。
- アセットではなく外部クライアントとして動作するプロセスはどれか。
- 参加者間で共有されるPDU定義／設定はどれか。
- どのPDU producerとconsumerによってデータフローを確認するか。
- どのBridge、Endpoint、RPC経路を使用するか。
- 単なる起動ではなく、実際の動作を証明する観測可能な証拠は何か。
- すべての長時間プロセスをどのように停止するか。

これらに答えられない場合は、実行できそうに見えるcommandを書くのではなく、Recipeを `unknown`、`partially_feasible`、`blocked` のいずれかとして扱ってください。

## AIでよくある失敗パターン

次のような誤りを避けてください。

- リポジトリREADMEにあるcommandを、そのまま完全な箱庭Recipeとして扱う。
- ソースコード内をkeyword検索しただけで、実行可能性があると主張する。
- PDU設定を特定せずにsimulatorを起動する。
- 必要なアセット登録が完了する前に `hako-cmd start` を実行する。
- シミュレーション時刻が存在する前にcontrollerを起動する。
- 1つの構成済みデモで複数のConductor ownerを起動する。
- `hako-cmd stop` をプロセスcleanupと同一視する。
- Bridge、viewer、HTTP server、simulatorのプロセスを起動したまま残す。
- Catalogに存在しないcomponent IDを捏造する。
- browser viewerをsimulatorと呼ぶ。
- PDU契約を明示せずに「PDU-compatible」と主張する。
