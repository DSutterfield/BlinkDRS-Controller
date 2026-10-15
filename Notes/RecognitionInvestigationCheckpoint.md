# Recognition investigation checkpoint — controller 1.2.11

Use this release as the next investigation baseline. The Git tag `recognition-checkpoint-1.2.11` marks the tested recognition source. The commit also captures the earlier installed recognition dependencies from 1.2.8–1.2.10; separate Clip sync edits are excluded.

## Preserved requirements

Keep Person detection and individual identification requirements unchanged. Type recognition may use native animal crops down to 16 pixels with existing quality checks; individual identification retains its existing 48-pixel and quality requirements. The temporal fallback is limited to uniquely matched adjacent small-animal tracks without individual-ID features. A weak view cannot start a track or become a strong hint for another weak view.

## Verified baseline

83 regression checks passed. Same Clips: 26076, 26078, 26079, 26093, 26094, 26095, 26096, 26097, 26098. All Person results match the prior installed build, including five appearances in 26078. Cream Dog 26076 and black Dog 26095 remain Dog. Stationary and climbing Cats remain recognized, the empty control 26097 stays empty, and 25 reviewed background regions produce no Cat/Dog labels. All 31 accepted animal crops were reviewed.

26079 now recognizes all six sampled views of the walking Cat through 9.36 seconds. New recoveries versus 1.2.10 are at 1.87, 5.62 and 9.36 seconds. The last view uses current moderate Cat evidence plus a uniquely matching adjacent strong Cat track; Type thresholds and final multi-view verification remain unchanged. Evaluation Clips were excluded from training; reference galleries were frozen.

## Next investigation

Start by collecting fresh missed Cat views across cameras, distances, lighting and stationary poses. Check the two still-Unknown appearances in 26094 before making a species claim. Examine brief activity between the 16 temporal samples and any animals not found by the scene search. Validate precision on fresh backgrounds and review background queue throughput before expanding the search further. These are investigation targets, not promises that the current scene contains additional visible animals.

Continue Type recognition work before individual identification unless Dan redirects the investigation. Do not train on the evaluation Clip frames to make these comparisons pass. Preserve recordings, catalogs, human confirmations and identity profiles.

## Recovery and evidence

Release details and required external model asset: `SpeciesRecognition-1.2.11.txt`.
Pi backups: `/home/dan/BlinkDRS-Controller/.temporal-install-20261014/backup`.
Pi test evidence: `/home/dan/BlinkDRS-Controller/.remaining-temporal-catio-20261014`.
Local test evidence: `C:/Users/DanSu/Documents/Codex/2026-10-14/g/outputs/RemainingCatioTests`.
Installation evidence: `C:/Users/DanSu/Documents/Codex/2026-10-14/g/outputs/InstalledRemainingCatio`.
