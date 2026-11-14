# DCASE Task 2 survey: what could help effente

Research only, written 13 Nov 2026. Nothing here was run by us. "(read)" means I read the report text myself; "(summary)" means a fetch tool's summary of a page, so check before relying on it. Unverified items are listed at the end.

## 1. How the task works

All years: train on normal clips only, score each test clip, rank by AUC and pAUC (p = 0.1, i.e. false-positive rate up to 10 %). From 2021 the official score is the harmonic mean of AUC and pAUC over machine types and domains ([2025 overview](https://arxiv.org/abs/2506.10097)).

| Year | What changed | Baseline | Winner (eval official score) |
|---|---|---|---|
| 2020 | normal-only, one model per machine ID; AUC/pAUC, mean rank | autoencoder | Giri (Amazon): self-supervised classification + ensemble ([report](https://dcase.community/documents/challenge2020/technical_reports/DCASE2020_Giri_103_t2.pdf), title read) |
| 2021 | domain shift: ~1000 source clips, 3 target | AE; MobileNetV2 | Lopez_IL 66.8 (summary) |
| 2022 | domain unknown at test, one threshold for both; 990 source / 10 target | AE; MobileNetV2 outlier exposure | Liu_CQUPT 71.0 (summary) |
| 2023 | first-shot: eval machine types differ from dev; no attributes or domain in eval | AE (MSE, Mahalanobis) | Jie_IESEFPT 67.0 (summary) |
| 2024 | attributes hidden for some machines | same AE | Lv_AITHU 66.2 vs baseline 56.5 (summary) |
| 2025 | optional clean-machine or noise-only clips; earlier years' data allowed | same AE | Wang_MYPS 61.63, Saengthong 61.57, Yang_NBU 61.20 (summary) |
| 2026 | "noise-aware": two channels, near and far mic; earlier data banned | AE, derived from 2025 | Fujimura_MERL 70.24; baseline 59.80 (summary) |

Results pages: [2020](https://dcase.community/challenge2020/task-unsupervised-detection-of-anomalous-sounds-results), [2021](https://dcase.community/challenge2021/task-unsupervised-detection-of-anomalous-sounds-results), [2022](https://dcase.community/challenge2022/task-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring-results), [2023](https://dcase.community/challenge2023/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring-results), [2024](https://dcase.community/challenge2024/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring-results), [2025](https://dcase.community/challenge2025/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring-results), [2026](https://dcase.community/challenge2026/task-first-shot-unsupervised-anomalous-sound-detection-for-machine-condition-monitoring). The baseline is tiny (0.27 M parameters, 0.17 GMAC on 128-bin log-mel; 2025 page, summary). Dev scores do not predict eval scores, and source and target AUC are negatively correlated across teams (2025 overview, summary).

## 2. What works

- **Fine-tuned pretrained encoder + kNN** (the 2023 to 2025 winners). AITHU 2024 (read) fine-tunes BEATs / EAT (full or LoRA) by classifying machine type + attribute with ArcFace, then scores by cosine 1-NN, and fuses 3 to 7 models by z-scored weights. Single models reach 63.5 to 65.8 on dev; the ensemble 68.0, i.e. fusion adds roughly 2 to 4 points. [AnoPatch](https://arxiv.org/abs/2406.11364) is the same recipe.
- **Frozen encoder + kNN + normalisation, no training** ([GenRep](https://arxiv.org/html/2409.05035v1), ICASSP 2025). On the 2023 eval set it gets 73.8 against 67.0 for the 2023 winner (summary). Intermediate layers (BEATs layer 4 or 5) beat the last layer; pooling over time only (keeping the frequency and channel dimensions) beat pooling over space or frequency. The 2025 extension ([report](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Saengthong_18_t2.pdf), read) reached 2nd place and shows normalisation matters: dev 64.5 for an ensemble of 5 frozen encoders with domain-wise local-density normalisation, 61.5 with a plain Z-score, baseline 56.3.
- **Small frozen encoder is nearly as good:** CED-tiny alone (5.49 M parameters, 1.33 GMAC per 10 s clip) scores 62.15 on dev (same report, read). BEATs is 90.7 M / 45 GMAC, EAT-large 303 M / 155 GMAC. Single-vs-ensemble is confounded, so this is a hint, not a ranking.
- **Backend matters more than pooling** ([Zhou & Wang 2026](https://arxiv.org/html/2606.19269), summary of HTML): on frozen BEATs, the spread across scoring backends is 13.75 AUC points on average against 3.23 across pooling choices. On the fan's target domain, kNN 55.7, Mahalanobis 40.1, density-normalised kNN 86.3, **PCA residual 32.6**. This is one backbone and the target domain, with a fixed 90 % variance, so it is a warning for beats-test's PCA arm, not proof. Label-free z-score min fusion of the four backends came within about 1 point of picking the best backend per machine (63.3 vs 64.4 harmonic-mean target AUC).
- **Local-density normalisation** ([Wilkinghoff et al.](https://arxiv.org/abs/2509.10951), abstract only): divide a clip's kNN distance by how dense the matched reference point's own neighbourhood is. [Pooling study](https://arxiv.org/abs/2603.04605) (abstract only): generalised-mean and relative-deviation pooling beat mean pooling on five datasets.
- **Weak cases:** a HuBERT + kNN report reached 50.96, only 1 point over the baseline ([Kret](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Kret_63_t2.pdf), read; it has odd details, so low trust). Choice of backbone, layer and normalisation clearly matter.
- **Other tricks:** enhancement front ends trained on supplementary clean or noise-only clips ([Fujimura/Nagoya 2025](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Fujimura_73_t2.pdf), read; dev 64.9); a small from-scratch classifier with ArcFace also placed 3rd (summary).

## 3. Single machine, no labels: what carries over

Carries over (no labels needed): frozen encoder; intermediate layer; train-set standardisation before kNN (Chung 2025, [report](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_CHUNG_40_t2.pdf), read); kNN, local-density kNN, Mahalanobis, PCA; z-score fusion of detectors or encoders; k-means or random subsampling of the reference bank.

Needs multi-machine labels or a second domain (does not carry over directly): fine-tuning by machine-type/attribute classification (all top systems of 2024); pseudo-labels by clustering; SMOTE and MemMixup for the 10 target clips; domain-wise Z-score or domain-wise density normalisation; per-domain memory banks. With one machine in one domain these reduce to plain kNN on one bank. A one-off LoRA fine-tune on public DCASE machines, then frozen on our device, is possible but costly and its transfer to our machine is untested.

## 4. Fit for the UNO Q (4x A53, once a minute)

- Memory bank: a week at one clip a minute is about 10 k vectors; at 768 floats that is about 31 MB and a brute-force kNN is a few MFLOP. Not a problem.
- Encoder cost per 10 s clip: the baseline AE is trivial; CED-tiny 1.3 GMAC; BEATs 45 GMAC. **No A53 timing exists.** My rough guess is seconds for BEATs, well under a second for CED-tiny, which would fit a 60 s budget. This is a guess; `audio_embed.py cost` measures a laptop, not the board.

## 5. False alarms and thresholds

- DCASE ranks by AUC and pAUC (false-positive rate up to 0.1). Nothing in it measures catches at zero false alarms. The organisers publish F1, precision and recall from submitted 0/1 decisions but do not rank on them and warn against tuning thresholds on the eval data (2025 page, summary).
- [F1-EV](https://arxiv.org/abs/2312.09143) (Wilkinghoff & Imoto, read): AUC ignores how hard it is to estimate a threshold from normal data alone; typical practice is a high percentile (e.g. 90th) of normal scores, which assumes the normal test scores match the training scores. Their point supports our metric: a large gap between the highest normal score and the anomalies matters more than AUC.
- DCASE 2026 reports on threshold rules that stop unreliable normal-score tails from causing false alarms came up only as search snippets; I could not read them.
- Dev test sets have 100 normal clips per machine, so "0 false alarms" there only shows a rate below about 3 %. Report counts with that caveat.
- Our own point, not from DCASE: DCASE clips are independent recordings, but our minutes are near-duplicates, so a kNN threshold taken from random out-of-sample training scores is too low. Exclude neighbours within hours when setting it.

## 6. Ideas for our device, ranked by expected gain against cost

1. **Frozen kNN with train standardisation, one intermediate layer and local-density normalisation, added as one more group in the group-balanced score** (low cost; strong DCASE evidence for ranking; effect on zero-false-alarm catches unknown).
2. **Small encoder first** (CED-tiny or the already-tested EfficientAT) before BEATs: 17x fewer parameters, 34x fewer MACs (low cost; fits the board).
3. **Z-score fusion of several backends and of encoders** (kNN, density kNN, Mahalanobis, our hand-feature PCA) instead of choosing one (low cost; guards against a backend that fails, like PCA on fan).
4. **Time-blocked threshold and rolling reference bank** (low cost; my extrapolation, matches our persistence watch).
5. Pooling variants (GeM, RDP): small expected gain.
6. One-off LoRA fine-tune on public machines; noise-only enhancement: high cost, needs labels or extra recordings. Park.

## 7. Experiments to add to the BEATs test (on the 5 dev sets)

1. Layer sweep; choose one layer for all machines (picking per machine inflates scores, as GenRep's own Table 1 does).
2. Standardise embeddings with training mean and std, then kNN with k = 1, 3, 5.
3. Local-density normalisation (K about 16, reference-point density) next to plain kNN.
4. Backends side by side: kNN, density kNN, Mahalanobis, PCA residual; then z-score mean and min fusion.
5. Pooling: mean vs GeM vs RDP.
6. Checkpoint: add the AudioSet-fine-tuned BEATs checkpoint GenRep seems to use (the test uses `BEATs_iter3_plus_AS2M.pt`; whether GenRep's "ft" means this is my reading, unverified).
7. Add CED-tiny and CED-base to MN10 and BEATs.
8. Score only source-domain test clips (our case) or add the 10 target clips to the bank; report AUC, pAUC, catches at zero false alarms and the gap between the highest normal and the anomalies, with the normal-clip count.
9. Fuse the best embedding score with our hand-feature PCA score per group.

## 8. Could not verify

Winner methods for 2021 to 2023 and 2026 beyond what the results pages give; Wilkinghoff 2024 (FKIE) and Jie 2023 reports (text read only in part, not summarised here); Wang_MYPS (rank 1, 2025), Yang_NBU and the 2026 winner's methods (two saved 2025 reports could not be matched to those teams, so I did not cite them); whether the 2025 overview and results pages agree on machine counts (they do not); the AE baseline's threshold rule; GenRep's meaning of "ft"; the Zhou & Wang and pooling papers beyond abstracts or the HTML summary; all A53 timings.

## 9. Sources

Overviews: [2020](https://arxiv.org/abs/2006.05822), [2021](https://arxiv.org/abs/2106.04492), [2022](https://arxiv.org/abs/2206.05876), [2023](https://arxiv.org/abs/2305.07828), [2024](https://arxiv.org/abs/2406.07250), [2025](https://arxiv.org/abs/2506.10097). Reports read: [AITHU 2024](https://dcase.community/documents/challenge2024/technical_reports/DCASE2024_Lv_110_t2.pdf), [Zhou 2025](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Zhou_13_t2.pdf), [Zhong 2025](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Zhong_34_t2.pdf), [Kret 2025](https://dcase.community/documents/challenge2025/technical_reports/DCASE2025_Kret_63_t2.pdf), [a 2026 GenRep dual-channel report, first page only; I assume it is Huang_14](https://dcase.community/documents/challenge2026/technical_reports/DCASE2026_Huang_14_t2.pdf) (it confirms the training-free GenRep line is now standard), plus those linked above. Other: [review of domain-shift handling](https://arxiv.org/abs/2503.10435), [local-density normalisation: Mind the Gap](https://arxiv.org/abs/2602.18777), [2026 results news](https://dcase.community/articles/dcase2026-challenge-results-published), [GenRep code](https://github.com/Phuriches/GenRepASD).
