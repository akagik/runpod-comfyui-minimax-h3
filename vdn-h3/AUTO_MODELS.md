# VDN-H3 0.1.2: 起動時の不足モデル自動準備

## 何が変わるか

`SSH起動 → /workspaceのmount確認 → 索引照合 → 不足分DL → SHA256確認 → resident ComfyUI起動`。
既存0.1.1のGPU常駐保護・モデル・精度・ComfyUI・依存関係・samplerは変えない。
モデルはDocker imageに含めず、`/workspace/models`へ置く。
Network Volume、Pod volume diskのどちらも使えるが、後者はPod削除時には残らない。
モデルの自動準備はManagerのジョブ投入前に完了する。自動生成・workflow登録はしない。

## Templateに設定する環境変数

```text
MODE_TO_RUN=pod
RUNPOD_VOLUME_ROOT=/workspace
REQUIRE_MINIMAX_MODELS=false
VDN_MODEL_PROFILE=i2va
MODEL_AUTO_DOWNLOAD=1
MODEL_VOLUME_CAPACITY_GB=200
VDN_ACCEPT_MODEL_LICENSE=1
```

`MODEL_VOLUME_CAPACITY_GB`は**実際に契約したVolume容量（十進GB）を設定する必須値**。
値を増やしても契約容量は増えない。ライセンス条件・利用資格を確認した利用者のみ
`VDN_ACCEPT_MODEL_LICENSE=1`を設定する（公開imageの既定値は0）。必要ならHF読み取り
トークンをRunPodのSecret経由で渡す。トークンをDockerfile、文書、公開Templateへ埋め込まない。
SSHはRunPod経由の公開鍵設定、portsは22/tcpと8188/http、起動コマンドはimage既定を使う。

| profile | 自動準備対象 | 固定モデル容量 |
| --- | --- | ---: |
| `i2va`（既定） | FL2VA BF16本体 + 共通encoder/VAE/VDN | 129.06 GB / 12ファイル |
| `ref2va` | Ref2VA full BF16本体 + 共通encoder/VAE/VDN | 129.06 GB / 12ファイル |
| `both` | 両本体 + 共通部分を重複なしで | 195.35 GB / 13ファイル |

既存の別profile用モデルは**削除しない**。200GBにI2VAがある状態でRef2VAを追加すると、
モデルだけで195.35GBになる。10GB安全余裕を確保できない場合はDLせず停止する。
両方を常置する新規構成は、出力・キャッシュ分を含め300GB程度を検討する。
容量変更・新規課金・既存モデル削除は別途承認が必要。

`/workspace`の`df`は共有MFS全体の巨大な空き容量を表示することがある。
スクリプトは契約容量からVolume内実ファイルの論理サイズを引いた推定値と、`df`の小さい方で判定。
同時に書く別プログラムやストレージ側snapshotまでは把握できないため、残量保証ではない。
DL途中の容量不足も生成受付前の失敗として扱う。自動拡張はしない。

## 索引と再利用

- `models.lock.json`: 既存I2VAの12ファイルの固定revision・保存先・サイズ・SHA256・URL。
- `models.ref2va.lock.json`: Ref2VA本体1ファイルの追加索引。
- `bootstrap_models.py --profile ref2va --list`: 共通分を含む選択結果を表示（DL・書込なし）。
- 最新版の再検索やリポジトリ一括DLはしない。WAN/Anima/Sol/FastH3は取得しない。
- 初めて見る既存ファイルと新規DLは全SHA256照合。初回は129GB読み出し分の時間がかかる。
- 次回は検証記録とサイズ/mtime/ctime/inode/deviceが一致するファイルを再利用し、再DLも全hash読出しも省く。
  これは起動高速化用で、metadataが変わらないbit rot検出の保証ではない。定期監査は`--verify-full`。
- サイズ/hash不一致の既存モデルは上書き・削除せず失敗する。管理者が確認してから復旧する。
- DLはVolume上のSHA別stagingでHF SDKの途中再開を使う。検証後、同一filesystemのhardlinkで
  正式名へ原子的に配置し、**自分のstaging側linkだけ**を解除する。モデルを二重コピーしない。
- Volume全体でbootstrap専用`flock`を取得する。並行bootstrapは最大1時間待つ。
  共有ストレージのlock/hardlink未対応時は安全側に失敗する。別ツールのDLはこのlockに従わない。

## 状態確認と失敗時

```bash
tail -f /workspace/model-bootstrap/boot-*.log
cat /workspace/model-bootstrap/status.json
```

状態は`CHECKING`/`VERIFYING`/`DOWNLOADING`/`INSTALLED`/`MODELS_READY`/`FAILED`。
`MODELS_READY`はモデル準備完了であり、ComfyUIの起動完了・GPUウォーム完了ではない。
その後Managerでworker readinessを確認し、承認済みのMDを`rcmctl`経由で投入する。
自動生成・GPUロード/アンロード・モデル切替はしない。

mountや設定検査の段階で失敗した場合はstatus.jsonが未作成のことがある。
mountがなければログは`/tmp/vdn-model-bootstrap.log`。全ての失敗でComfyUIは起動せず、
コンテナとSSHは残す。自動リトライや自動Pod停止はしないため**課金は継続する**。
ネットワーク障害後は設定を確認し、明示的な次回起動で同じstagingから再開できる。
手動で検証/DLだけを再実行しても、待機中supervisorは自動でComfyUIを開始しない。

```bash
# DL禁止の検証（不足なら失敗、既存正常ファイルの検証記録は作成）
MODEL_AUTO_DOWNLOAD=0 /opt/comfyui-venv/bin/python /opt/vdn-h3/scripts/bootstrap_models.py
# 全hash監査（モデル準備のみ。生成/アンロード/再起動なし）
MODEL_AUTO_DOWNLOAD=0 /opt/comfyui-venv/bin/python /opt/vdn-h3/scripts/bootstrap_models.py --verify-full
```

公開imageの更新と稼働Podへの反映は別操作。**imageをbuild/pushしても既存Podは変わらない**。
稼働中Podのimage差し替え/再起動やresident loaderのモデル変更は、ウォーム済みモデルを失うため
別途明示承認が必要。`both`を準備しても、同一residentで自由にGPUモデル切替できる意味ではない。
Ref2VAのworkflow・品質/GPU動作は、別の実生成テストで確認する。

## Buildと検証

```bash
python3 -m unittest discover -s tests -v
docker buildx build --platform linux/amd64 -f Dockerfile.autoboot \
  -t ghcr.io/akagik/runpod-comfyui-minimax-h3:vdn-h3-0.1.2 --load .
```

unit testsは既存再利用/全hash/不足のみDL/途中失敗再開/容量/mount/lock/競合上書き防止を検証。
ローカルCPUテストと実際のRunPod MFSでの全量起動テストは区別する。
公開に含めるのは汎用script・固定索引・テスト・本文書のみ。個人の画像/プロンプト/出力は含めない。

参考: [HFのrevision/local_dir download仕様](https://huggingface.co/docs/huggingface_hub/guides/download)、
[RunPodストレージ](https://docs.runpod.io/pods/storage/types)。
