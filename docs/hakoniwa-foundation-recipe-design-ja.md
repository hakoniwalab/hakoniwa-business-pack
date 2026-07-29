# 箱庭 Foundation と個別 Recipe の設計仕様

## 1. 目的

本ドキュメントは、Hakoniwa Business Pack における次の二つの責務を分離するための設計仕様です。

- 複数の Recipe から再利用する箱庭基盤を準備・管理する **Foundation**
- ユーザーの目的に必要な構成と実行方法を定義する **個別 Recipe**

ここでいう Foundation は、特定の Drone、Robot、Viewer、Demo に依存するセットアップ手順ではありません。箱庭の共通基盤コンポーネントを Business Pack の作業ディレクトリ内へ一度インストールし、複数の Recipe から再利用するための仕組みです。

個別 Recipe は Foundation のビルド手順を複製しません。個別 Recipe が宣言するのは、必要な Capability、容量、Interface、Artifact などの要求です。Foundation はその要求と現在のローカルインストールを比較し、既存インストールを再利用できるか、不足部分の構築が必要かを判定します。

Foundation はインストール済みソフトウェアだけを意味しません。箱庭コアの設定と共有ランタイム領域も、複数の Recipe から共通利用する Foundation の一部として管理します。一方、PDU 定義、アセット構成、Bridge の転送設定、Viewer 設定など、実行するシステムのトポロジを表すものは Recipe ごとに管理します。

本仕様は、まず設計上の合意を作ることを目的とします。ファイル形式、CLI、比較規則の細部は、本ドキュメントを人間および AI でレビューした後に実装します。

## 2. 設計原則

### 2.1 Foundation は個別 Recipe に依存しない

Foundation は、次のような個別用途を名前や構成に含めません。

- Drone Three.js Demo
- Shadow Hand Foxglove Demo
- TurtleBot3 MuJoCo Demo
- 特定顧客向けシステム

Foundation は、複数の用途で利用できる箱庭基盤を提供します。

```text
Hakoniwa Local Foundation
  |
  +-- Core runtime
  +-- Core configuration and shared mmap
  +-- PDU Endpoint capabilities
  +-- PDU Bridge capabilities
  +-- Python launcher/runtime support
  `-- installed build information
```

個別 Recipe は、この Foundation に対する要求を宣言します。Foundation の component ID には、Catalog とリポジトリで使用している `hakoniwa-core-pro` などの安定した ID をそのまま使用します。初期設計では、これとは別の抽象的な provider ID や capability resolver 層を追加しません。

### 2.2 Foundation はクロスプラットフォームとする

Foundation の操作入口は、各コンポーネントが所有する `tools/hako.py` を使用します。

```text
Foundation resolver
        |
        v
component/tools/hako.py
        |
        +-- POSIX native driver
        +-- Windows native driver
        `-- CMake / component-owned tools
```

Foundation Recipe 自体に macOS、Linux、Windows 固有のビルドコマンドを並べません。OS、CPU architecture、toolchain の解決は `hako.py` と各コンポーネントの実装へ委譲します。

インストール済み情報には実際のPlatformを記録します。これはFoundationを特定OSへ制限する情報ではなく、現在のバイナリがどのPlatform向けに構築されたかを判定する情報です。

### 2.3 システムディレクトリへインストールしない

Foundation は、原則として次のようなシステムディレクトリを使用しません。

```text
/usr
/usr/local
/etc/hakoniwa
/var/lib/hakoniwa
```

標準のインストール先、共通設定、実行時状態は、Business Pack リポジトリ内の `work/` 配下とします。

これにより、次を実現します。

- 管理者権限を必要としない
- ユーザーの既存箱庭インストールを上書きしない
- Business Pack ごとに構成を分離できる
- インストール済みバイナリとビルド構成の対応を追跡できる
- CI、開発PC、検証PCで同じ操作モデルを利用できる

### 2.4 Foundation は再利用を優先する

Foundation は、Recipeを実行するたびにビルドしません。

```text
Recipe requirements
        |
        v
Installed Foundation inspection
        |
        +-- SATISFIED    -> reuse
        +-- MISSING      -> install missing component
        +-- INCOMPATIBLE -> rebuild affected dependency chain
        `-- UNKNOWN      -> report and require an explicit decision
```

初回セットアップと通常実行を明確に分離します。

### 2.5 完全一致ではなく充足関係を判定する

Recipeの要求とインストール時manifestをファイル単位で完全一致比較してはいけません。

次のような設定差は、インストール済みバイナリの再構築理由にならない場合があります。

- build directory
- parallel数
- testsの有効・無効
- examplesの有効・無効
- ログ出力先
- `--dry-run`の利用有無

Foundation は、Component Receiptに記録されたCapability、build limit、Platform、依存関係など、再利用可否に必要なフィールドだけをRecipe要求と比較します。

この比較対象を概念上 **Install Contract** と呼ぶことはできますが、独立したファイルやschemaは作りません。Install Contractは「Receiptのうち再利用判定に使うフィールド」を意味します。Contract Hashも使用しません。

## 3. 全体アーキテクチャ

```text
Component Catalog
  - hako.py support
  - capabilities
  - interfaces
  - dependencies
        |
        v
Common Foundation Definition
  - local workspace layout
  - install receipt schema
  - comparison rules
  - known dependency order
        |
        v
Individual Recipe
  - foundation requirements
  - recipe-specific configure inputs
  - runtime topology
  - launch and verification
        |
        v
Foundation Resolver
  - inspect installed receipts
  - normalize requirements
  - calculate satisfaction
  - invoke component-owned hako.py when needed
        |
        v
Business Pack Local Foundation
```

責務の境界は次のとおりです。

| レイヤ | 責務 |
| --- | --- |
| Catalog | コンポーネントが提供するCapability、Interface、依存関係、`hako.py`対応状況 |
| Foundation定義 | ローカル配置、Core共通設定、共有runtime、installed receipt、比較、再構築範囲、共通操作 |
| 個別Recipe | Foundationに必要な要求、PDU・Asset・接続・表示などの固有設定、Demo実行と検証 |
| Component repository | manifest schema、doctor、configure、build、test、install、smokeの実装 |

Business Pack は、Core、Endpoint、Bridge のCMakeロジックを再実装しません。各コンポーネントの `hako.py` を呼び出し、結果として配置されたArtifactとreceiptを管理します。

## 4. ローカルワークスペース

標準レイアウト案は次のとおりです。

```text
hakoniwa-business-pack/
└── work/
    ├── foundation/
    │   ├── install/
    │   │   ├── bin/
    │   │   ├── lib/
    │   │   ├── include/
    │   │   └── share/
    │   │       └── hakoniwa/
    │   │           └── receipts/
    │   │               ├── hakoniwa-core-pro.yaml
    │   │               ├── hakoniwa-pdu-endpoint.yaml
    │   │               ├── hakoniwa-pdu-rpc.yaml
    │   │               ├── hakoniwa-pdu-bridge-core.yaml
    │   │               └── hakoniwa-pdu-python.yaml
    │   ├── python/
    │   │   ├── bin/python
    │   │   └── lib/python3.12/site-packages/
    │   ├── config/
    │   │   └── cpp_core_config.json
    │   ├── runtime/
    │   │   └── mmap/
    │   ├── build/
    │   │   ├── hakoniwa-core-pro/
    │   │   ├── hakoniwa-pdu-endpoint/
    │   │   ├── hakoniwa-pdu-rpc/
    │   │   ├── hakoniwa-pdu-bridge-core/
    │   │   └── hakoniwa-pdu-python/
    └── recipes/
        └── <recipe-id>/
            ├── config/
            ├── assets/
            ├── missions/
            ├── logs/
            └── validation/
```

標準パスは、Business Pack root から解決します。

```text
foundation root:
  <business-pack-root>/work/foundation

install prefix:
  <business-pack-root>/work/foundation/install

runtime state:
  <business-pack-root>/work/foundation/runtime

recipe workspace:
  <business-pack-root>/work/recipes/<recipe-id>
```

`work/`はローカル生成物であり、Git管理対象にしません。

Foundation の領域はすべての Recipe から共通利用します。Recipe の起動ごとに `run-id` やプロセス ID を使ったディレクトリは作成しません。`work/recipes/<recipe-id>/` は同じ Recipe の再実行時に再利用し、ログや検証結果は上書きまたは明示的に初期化します。

### 4.1 FoundationとRecipeの配置境界

配置先は、データのライフサイクルと意味で分類します。

| 配置先 | 管理対象の例 |
| --- | --- |
| `work/foundation/install/` | 実行ファイル、ライブラリ、header、CMake package、Foundation共通Python venv、component receipt |
| `work/foundation/config/` | `cpp_core_config.json`など、Recipeに依存しない箱庭共通設定 |
| `work/foundation/runtime/` | Coreが共通利用するmmapなどのruntime状態 |
| `work/foundation/build/` | componentごとのローカルbuild tree |
| `work/recipes/<recipe-id>/config/` | PDU定義、Fleet、Bridge転送、Launcher、Viewer設定 |
| `work/recipes/<recipe-id>/assets/` | Recipe固有Asset |
| `work/recipes/<recipe-id>/missions/` | Recipe固有Mission |
| `work/recipes/<recipe-id>/logs/` | Recipe実行ログ |
| `work/recipes/<recipe-id>/validation/` | 最新のRecipe検証結果 |

判断基準は次のとおりです。

```text
Recipeを切り替えても同じものを利用する
  -> Foundation

実行するシステムのPDU、Asset、接続、表示、Missionを定義する
  -> Recipe
```

### 4.2 パスの移植性

設計上のパスは、可能な限りBusiness Pack rootからの相対表現で保持します。CMakeや実行プロセスへ渡す直前に絶対パスへ解決します。

receiptへ絶対パスを記録する場合も、移動可能な情報と現在の解決結果を分離します。

```yaml
install:
  root_relative_path: work/foundation/install
  resolved_path: /current/workspace/hakoniwa-business-pack/work/foundation/install
```

Workspaceを移動した場合は、Artifact自体がrelocatableかをdoctorで確認します。RPATHや生成済み設定に古い絶対パスが含まれる場合は、単純なパス再解決ではなく再installまたは再buildが必要です。

## 5. ローカル実行環境

Foundationから実行するプロセスには、ローカルprefixを明示的に渡します。

概念上の環境は次のとおりです。

```text
HAKONIWA_HOME=<foundation-install-prefix>
HAKO_CONFIG_PATH=<business-pack-root>/work/foundation/config/cpp_core_config.json
CMAKE_PREFIX_PATH=<foundation-install-prefix>
PATH=<foundation-install-prefix>/bin:<existing-path>
PYTHON=<foundation-install-prefix>/python/bin/python
```

動的ライブラリ検索パスはOSごとに `hako.py` またはLauncher環境設定で解決します。

```text
Linux:
  LD_LIBRARY_PATH

macOS:
  DYLD_LIBRARY_PATH

Windows:
  PATH
```

個別RecipeやLauncherは、`/usr/local/hakoniwa`をハードコードしません。Foundation resolverが解決したprefixを、Recipe固有のconfigure処理へ渡します。

### 5.1 Foundation共通Python venv

Python runtimeはRecipeごとに作らず、Foundation install prefix内の共通venvとして管理します。

```text
<foundation-install-prefix>/python/
```

`hakoniwa-pdu`はこのvenvへ通常の`pip install`で導入します。Receipt生成前とsmoke時に、venvのPythonで次を確認します。

```bash
<foundation-install-prefix>/python/bin/python -m pip show hakoniwa-pdu
```

`Location`が同じFoundation venv内であることを必須とし、ユーザー環境や`/usr/local`にある別packageを誤認しません。

Coreの`hakopy`とEndpointのCFFI package `hakoniwa_pdu_endpoint`も同じvenvからimportできるようにします。LauncherはこのvenvのPythonを使用するため、Core binding、Endpoint CFFI、PDU Pythonの組合せが一意になります。

ここでの「同じPython」は、`3.12`というminor versionだけでなく、Foundation
venvを作成したinterpreter実体を意味します。Coreのnative bindingとEndpoint
CFFIもそのinterpreterでbuildし、別のpyenv/Homebrew/system Pythonが暗黙に
選択されることを防ぎます。

### 5.2 Core configとmmap

Core configはFoundationの共通設定として配置します。install artifactとはライフサイクルが異なるため、install prefixの配下には置きません。

```text
work/foundation/config/cpp_core_config.json
```

`core_mmap_path`もFoundation内のruntime pathを指定します。

```json
{
  "shm_type": "mmap",
  "core_mmap_path": "<business-pack-root>/work/foundation/runtime/mmap",
  "asset_timeout_usec": 600000000
}
```

これにより、`/etc/hakoniwa`および`/var/lib/hakoniwa`へ依存しないローカル実行を可能にします。

Core configとmmap pathはRecipe configureで作り直しません。RecipeはFoundationが提供するCore環境を利用し、必要な容量やCapabilityを要求として宣言します。現在のFoundationが要求を満たさない場合は、Recipe固有のCore環境を作るのではなく、Foundationの更新計画を提示します。

## 6. Foundationの構成単位

初期段階では、一つのactive install prefixを管理します。

Foundationに含めるコンポーネントは、Catalogと実際のRecipe要求から決めます。すべての箱庭コンポーネントを無条件にインストールするものではありません。

代表的な基盤候補は次のとおりです。

- `hakoniwa-core-pro`
- `hakoniwa-pdu-endpoint`
- `hakoniwa-pdu-rpc`
- `hakoniwa-pdu-bridge-core`
- `hakoniwa-pdu-python`

Foundationは、これらの利用可能なCapabilityの集合を管理します。

```yaml
foundation:
  capabilities:
    core:
      shared_memory: true
      hako_cmd: true
    endpoint:
      hakoniwa_core: true
      core_callback: true
      core_polling: true
    rpc:
      pdu_native_request_response: true
    bridge:
      hakoniwa_app: true
      web_bridge: true
    python:
      hako_launcher: true
```

component IDには、CatalogおよびリポジトリのIDをそのまま使用します。

```text
hakoniwa-core-pro
hakoniwa-pdu-endpoint
hakoniwa-pdu-rpc
hakoniwa-pdu-bridge-core
hakoniwa-pdu-python
```

Recipeから実装componentを隠す追加のprovider抽象化は行いません。将来、同じ役割を提供する複数実装を選択する具体的な要求が生じた場合に、必要な範囲で拡張します。

## 7. 個別RecipeのFoundation要求

個別Recipeは、Foundationのビルド手順ではなく要求を宣言します。

例として、DroneとThree.jsを接続するRecipeは、概念的に次の要求を持ちます。

```yaml
foundation_requirements:
  hakoniwa-core-pro:
    capabilities:
      shared_memory: true
      hako_cmd: true

  hakoniwa-pdu-endpoint:
    capabilities:
      hakoniwa_core: true
      core_callback: true

  hakoniwa-pdu-bridge-core:
    capabilities:
      hakoniwa_app: true
      web_bridge: true
      web_bridge_fleets_config_format: true

  hakoniwa-pdu-python:
    capabilities:
      hako_launcher: true
```

この要求には、通常、次の項目を含めません。

- build directory
- parallel数
- test target
- example target
- CI専用設定
- 一時ログパス

### 7.1 Recipe固有のconfigure設定

個別Recipeには、Foundation要求とは別に、Recipe固有のruntime configure設定があります。

例えば次のような情報です。

- Drone fleet構成
- PDU定義
- Bridgeの転送対象
- WebSocket port
- Viewer config
- Launcher asset構成
- Foundation prefixを参照する実行ファイルパス

```text
Foundation requirements
  -> どの基盤Capabilityが必要か

Recipe configure
  -> その基盤を使って今回のシステムをどう構成するか
```

この二つを混同しません。

例えば `web_bridge_fleets_config` の実ファイルは、Recipeが定義する接続構成なのでRecipe workspaceへ生成します。一方、その設定形式を読み取れることはBridge componentのCapabilityとしてFoundationへ要求します。

Core config、Core mmap path、Core build limitsはRecipe固有configureに含めません。Recipeは必要なCore容量をFoundation requirementsとして宣言し、Foundation resolverが現在の共通Core環境で充足できるかを評価します。

## 8. Installed Build Information

各コンポーネントはinstall時に、インストールしたバイナリと同じprefixへbuild情報を配置します。

推奨パスは次のとおりです。

```text
<install-prefix>/share/hakoniwa/receipts/<component-id>.yaml
```

### 8.1 Receiptの目的

Receiptは次を回答できる必要があります。

- どのコンポーネントか
- どのPlatform向けか
- どのversion/revisionか
- どのCapabilityを含むか
- どのbuild limitで構築されたか
- どの依存componentに対して構築されたか
- どのArtifactが配置されたか
- どのresolved manifestから生成されたか

### 8.2 Receipt案

```yaml
schema_version: 1

component:
  id: hakoniwa-pdu-endpoint
  version: 1.0.0
  source_revision: f395a92

platform:
  os: macos
  architecture: arm64
  toolchain: apple-clang

install:
  prefix: work/foundation/install

capabilities:
  hakoniwa_core: true
  core_callback: true
  core_polling: true
  zenoh: false
  mqtt: false
  python_binding: false

build_limits:
  asset_number: 128
  service_max: 1024
  recv_event_max: 4096
  service_client_max: 256
  client_namelen_max: 64
  service_namelen_max: 128
  channel_max: 8192

dependencies:
  hakoniwa-core-pro:
    version: 1.3.0
    source_revision: abc1234
    build_limits:
      asset_number: 128
      service_max: 1024
      recv_event_max: 4096
      service_client_max: 256
      client_namelen_max: 64
      service_namelen_max: 128
      channel_max: 8192

artifacts:
  - path: lib/libhakoniwa_pdu_endpoint.a
    kind: library
  - path: lib/cmake/hakoniwa_pdu_endpoint
    kind: cmake-package

configuration:
  resolved_manifest: share/hakoniwa/receipts/resolved/hakoniwa-pdu-endpoint.yaml
```

### 8.3 Receiptと比較対象

Receiptには、実際に解決された全設定を参照するresolved manifestを記録します。これは再現性と診断に利用します。

再利用可否は、Receipt内の次のようなフィールドをRecipe要求と直接比較して判定します。

- Platform
- Capability
- ABI互換性に影響するbuild limit
- 依存componentの情報
- Artifact

Install Contractという別データは生成しません。Component Receiptをinstalled stateの正とし、RecipeのFoundation requirementsをrequired stateの正とします。

## 9. 要求とインストール済み構成の比較

比較処理を概念的に `satisfies(installed, required)` と呼びます。

### 9.1 Capability

Capabilityは、原則としてインストール済み集合が要求集合を包含すれば充足します。

```text
installed capabilities ⊇ required capabilities
```

例:

```text
installed:
  core_callback = true
  core_polling  = true

required:
  core_callback = true

result:
  SATISFIED
```

### 9.2 Capacity

単純な容量値は、意味が明確な場合に限り、インストール値が要求値以上なら充足と判定できます。

```text
installed capacity >= required capacity
```

ただし、Coreのcompile-time定数がバイナリlayoutや共有メモリ互換性へ影響する場合、単純な大小比較だけでは不十分です。

次の二つを分離します。

- Recipeが必要とする最低容量
- インストール済み依存component間のbuild limitの整合

### 9.3 依存componentとの互換性

相互にリンクまたは共有メモリ契約を共有するインストール済みcomponentは、ABI互換性に影響するbuild limitが依存先と整合している必要があります。ABI contractという独立データは作らず、Receiptの`build_limits`と`dependencies`に必要な値を記録します。

```text
Core Receiptの現在のbuild_limits
       ==
Endpoint Receiptのdependenciesに記録されたCoreのbuild_limits
       ==
Bridge Receiptのdependenciesに記録された依存componentのbuild_limits
```

Coreの代表的なbuild limitは次のとおりです。

- asset number
- service max
- receive event max
- service client max
- client name length max
- service name length max
- PDU channel max

これらのどれがABI互換性へ影響するかは、Core側の実装契約として確定する必要があります。比較結果は、どの項目のrequired、installed、dependency情報が一致しないかを示します。

### 9.4 比較対象外の例

次の設定は、通常、ReceiptとRecipe要求の比較対象外です。

- build directory
- parallel数
- testの有効・無効
- examplesの有効・無効
- `--dry-run`
- 一時ファイルの出力先

ただし、ある設定が実際のinstall artifactを変える場合は比較対象へ昇格させます。

## 10. Recipe要求に対するFoundation評価状態

4状態はFoundationへ固定的に付与する属性ではありません。同じFoundationでもRecipeごとに要求が異なるため、次の評価結果として扱います。

```text
evaluate(Recipe requirements, Installed Foundation)
  -> SATISFIED / MISSING / INCOMPATIBLE / UNKNOWN
```

Foundation resolverは、Recipeが要求するcomponentごとに状態と理由を求め、それらをRecipe全体の評価状態へ集約します。

### `SATISFIED`

要求されたCapability、Artifact、Platform、build limitを現在のインストールが満たしています。

Build/installは実行しません。

### `MISSING`

必要なcomponentがインストールされていません。

不足componentを構築対象として提示します。

### `INCOMPATIBLE`

インストールは存在しますが、次のような不一致があります。

- Platform不一致
- architecture不一致
- 必要Capabilityが無効
- ABI互換性に影響するbuild limitの不一致
- dependency contract不一致
- 必須Artifact欠落

影響する依存チェーンを再構築対象として提示します。

### `UNKNOWN`

古いインストールなど、Receiptが存在しない、または必要な項目を読み取れず、安全に互換性を判定できません。

自動的に互換とみなしたり、無条件に上書きしたりしません。理由と選択肢をユーザーへ提示します。

### 10.1 Recipe全体への集約

集約規則は、少なくとも次を区別できる決定的な規則とします。

- 要求componentが存在しない場合は `MISSING`
- 存在するが要求を満たさない場合は `INCOMPATIBLE`
- Receipt不足などで判定できない場合は `UNKNOWN`
- すべての要求を満たす場合だけ `SATISFIED`

複数種類の問題が同時に存在する場合の表示優先順位は実装時に固定します。ただし、集約状態が一つでも、componentごとの状態と理由を失ってはいけません。

```yaml
recipe: drone-threejs
status: INCOMPATIBLE

components:
  hakoniwa-core-pro:
    status: INCOMPATIBLE
    reasons:
      - code: CAPACITY_INSUFFICIENT
        field: asset_number
        required: 256
        installed: 128

  hakoniwa-pdu-endpoint:
    status: SATISFIED
```

`status`と`reasons`は、ReceiptとRecipe requirementsから評価時に導出する一時的な結果です。キャッシュや永続状態として保存しません。

初期実装ではreason code体系を固定しません。ただし、人間とAIが再調査せず差分を説明できるよう、`field`、`required`、`installed`などの構造は返します。

## 11. 再構築範囲

依存関係に基づいて、変更の波及範囲を求めます。

基本例:

```text
Core変更
  -> Core
  -> Core-enabled Endpoint
  -> EndpointをリンクするRPC
  -> Hakoniwa-integrated Bridge

Endpoint変更
  -> Endpoint
  -> EndpointをリンクするRPC
  -> EndpointをリンクするBridge

RPC変更
  -> RPC

Bridge変更
  -> Bridge
```

MVPでは汎用的なdependency solverや最小再構築計算を実装しません。Catalogに記録された既知の依存順序を使い、安全側に倒して影響componentを再構築します。

Receiptの依存情報を使って再構築範囲を狭める最適化は、実際に必要になった段階で追加します。

## 12. Foundation操作フロー

```text
1. Recipe requirementsを読む
2. Foundation rootを解決する
3. Platformを検出する
4. installed Component Receiptsを読む
5. componentごとにsatisfies()と一時的なreasonを評価する
6. Recipe全体の状態へ集約する
7. SATISFIEDならFoundationを変更せず終了する
8. MISSING/INCOMPATIBLEなら既知の依存順序から再構築planを作る
9. component-owned hako.pyを依存順に実行する
10. local prefixへinstallする
11. Component Receiptを配置する
12. 再度satisfies()を評価する
13. 個別RecipeのconfigureへFoundation情報を渡す
```

Foundationの確認だけを行う場合、download、build、installを実行しません。

## 13. `hako.py`との契約

Foundationは、共通の操作語彙を利用します。

```text
doctor
configure
build
test
install
smoke
```

### 13.1 `doctor`

- toolchainと依存関係を確認する
- local FoundationのreceiptとArtifactを確認する
- 修正を自動実行しない
- 不一致理由を機械可読または明確なtextで返す

### 13.2 `configure`

- component-owned manifestを検証・解決する
- local install prefixを反映する
- resolved manifestを生成する

### 13.3 `build`

- configureで解決した同じ構成を使用する
- component-owned native driverへ委譲する
- installを暗黙にシステムディレクトリへ行わない

### 13.4 `test`

- build tree上のcomponent contract testを実行する
- test用build optionが必要な場合はcomponentのoperation semanticsで解決する
- install済みFoundationの動作確認とは区別する

### 13.5 `install`

- build済みArtifactをlocal prefixへ配置する
- 独立した二度目のconfigure/buildを行わない
- resolved manifestを保存する
- component receiptを保存する
- install prefixをcomponent固有実装へ正しく渡す

### 13.6 `smoke`

- install済みArtifactをlocal Foundation prefixから利用できることを確認する
- build treeを暗黙に参照しない
- 実機や外部システムへ接続しない範囲の軽量な確認を基本とする

理想的な呼び出し例:

```text
python tools/hako.py doctor --config <component-manifest>
python tools/hako.py configure --config <component-manifest>
python tools/hako.py build --config <component-manifest>
python tools/hako.py test --config <component-manifest>
python tools/hako.py install --config <component-manifest> --install-dir <foundation-prefix>
python tools/hako.py smoke --config <component-manifest> --install-dir <foundation-prefix>
```

`--install-dir`の名称と共通化範囲は、既存の`hako.py`契約との整合を確認して決定します。

各componentが6操作のすべてを直ちに実装しているとは限りません。Catalogは対応operationを明示し、Foundation resolverはそのcomponentが提供する範囲だけを呼び出します。ただし、Business Pack全体の標準語彙から`test`と`smoke`を除外しません。

### 13.7 Schemaの所有権

Foundationが共通化するのは操作と比較境界です。各component manifestのschemaは引き続きcomponent repositoryが所有します。

```text
common:
  config selection
  operation semantics
  install receipt boundary

component-owned:
  manifest schema
  native build implementation
  capability-specific validation
```

## 14. 個別Recipeの実行フロー

個別Recipeは次の順序で実行します。

```text
1. Foundation requirementsを評価
2. SATISFIEDでなければFoundationへ戻る
3. `work/recipes/<recipe-id>/config/`へRecipe固有configureを生成
4. Foundation prefixをlauncher/configへ反映
5. runtime preflight
6. assetsを起動
7. behaviorを検証
8. cleanup
```

個別Recipeのruntime preflightには、Foundation buildを含めません。

Core configとmmapはFoundation側の共通領域を利用します。Recipeの開始・終了時に、FoundationのCore configをRecipe固有値で上書きしたり、Recipe名やプロセスIDごとのmmap directoryを生成したりしません。

例えばWeb Demoでは、次のような変化しやすい実行時状態を確認します。

- portの空き
- 古いprocessの残存
- HTTP serverの応答
- WebSocket endpoint
- Browser接続
- PDU producerの動作

これはFoundationのインストール確認とは別の責務です。

## 15. 複数構成の将来対応

初期実装では、一つのactive Foundation installを管理します。

複数Foundationの切り替え、install cache、profile、snapshotは初期スコープに含めず、本仕様では形式も定義しません。互換性のない複数構成を頻繁に切り替える具体的な要求が生じた時点で設計します。

## 16. Agency Boundary

Business Pack内のlocal Foundationであっても、次の操作は区別します。

### 自動確認可能

- receiptの読み取り
- Artifact存在確認
- Platform確認
- ABI互換性に影響するbuild limitの比較
- component doctor
- build plan生成

### ユーザー要求後に実行可能

- local build
- local install
- local runtime directory作成
- simulation-only smoke test

### 明示的な確認が必要

- ネットワークからのdownload
- private repositoryへのアクセス
- commercial componentの利用
- 既存Foundationの互換性のない置換
- 実機や外部システムへの接続

Foundation doctorは、原則として検出と説明を担当し、暗黙のdownloadやinstallを行いません。

## 17. 現時点の実装ギャップ

本仕様を実現するには、少なくとも次の確認・実装が必要です。

### 共通

- Business Pack local Foundation rootの定義
- `work/`のGit除外
- Foundation requirement schema
- installed receipt schema
- Recipe単位の`satisfies()`、reason、集約規則
- Catalogの既知の依存順序を使う再構築plan

### `hako.py`

- Core Proの`install`対応
- PDU Endpointの`install`対応
- PDU RPCのFoundation prefix統合とreceipt生成
- PDU Bridge Coreの`install`対応
- PDU PythonのFoundation共通venvへの`pip install`、`pip show`確認、receipt生成
- Endpoint CFFI packageのFoundation共通venvへの配置
- local install prefixの共通した受け渡し
- install時のreceipt生成

### Core Pro

- POSIX build driverの固定install prefixの整理
- Core configをFoundationの共通config領域へ生成する方法
- mmap pathをFoundation配下へ解決する方法
- Core build limitsのうち、ABI互換性へ影響する項目の確定

### Recipe実行

- LauncherへFoundation prefixを渡す方法
- システムディレクトリをハードコードする既存launch構成のAdapter
- Foundation readinessとruntime readinessの分離

## 18. 初期実装スコープ案

設計レビュー後の最小実装は、次の順序を推奨します。

1. Component ReceiptとFoundation requirementsのschemaを確定する
2. Business Pack内のFoundation inspectorを作る
3. Core Proのlocal installとreceipt生成を実装する
4. Foundation共通Python venvとPDU Pythonのlocal install / receipt生成を実装する
5. Endpointのlocal install、CFFI配置、receipt生成を実装する
6. RPCのlocal installとreceipt生成を実装する
7. Bridge Coreのlocal installとreceipt生成を実装する
8. Recipe要求に対するcomponent評価、reason、全体状態の集約を実装する
9. 一つの既存RecipeからFoundation build手順を除去する
10. Foundation共通のCore configとmmap配置を実装する
11. Recipe固有configureからlocal Foundationを利用する

最初の検証対象としてDrone Three.js Demoを利用できますが、Foundationの設計や名前をDrone固有にはしません。

## 19. レビューで決める項目

本仕様のレビューでは、特に次を議論します。

1. Foundationの正式なIDと標準root
2. Receiptのうち再利用判定に使用する共通項目
3. Coreの7つのbuild limitをどう比較するか
4. Capabilityの真偽値、version、列挙値、容量値の充足規則
5. 複数問題がある場合のRecipe集約状態の表示優先順位
6. `hako.py install`の共通option名
7. 古いreceiptなしinstallを`UNKNOWN`としてどう移行するか
8. LauncherへFoundation prefixを渡す共通方式
9. Recipe workspaceのログとvalidationをいつ初期化するか

## 20. 完了条件

本設計の実装完了条件は次のとおりです。

- FoundationがBusiness Pack配下だけで構築・実行できる
- システムディレクトリへのinstallを必要としない
- 同じFoundationを複数Recipeから再利用できる
- Core configとmmapがRecipe非依存のFoundation領域で共有される
- PDU、Asset、接続、表示、Missionの設定がRecipeごとに分離される
- 通常実行ごとにrun-id directoryが増加しない
- Recipe変更だけでは不要な再ビルドが発生しない
- 4状態がRecipe要求とinstalled Foundationの比較結果として評価される
- Recipe要求とinstalled receiptの比較理由をcomponentごとに説明できる
- 既知の依存順序に従って安全な範囲を再構築できる
- install済みバイナリの構成と依存関係をreceiptから確認できる
- 個別RecipeがFoundation build/install手順を複製しない
- Foundation readinessとDemo runtime verificationが分離されている
- Drone Three.js Recipeの初回実行でFoundationを構築してDemoを実行できる
- 同Recipeの二回目の実行ではFoundationを再ビルドせずDemoを実行できる
