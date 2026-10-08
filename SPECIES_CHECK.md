# Species check — controller 1.2.6 (2026-10-08)

Before animal tracking and identity matching, rank Person/Cat/Dog using human-confirmed DINO Type references. Exclude the current clip, cap at 128 references per Type, balance Types and source clips, and require two independent clips/images supporting a correction. Require ranking score >=0.8, margin >=0.3 and second independent cosine >=0.5. Scores are not probabilities.

Strong cat/dog disagreements correct the suggested Type; strong person-as-animal errors are suppressed because face identity requires different features. Ambiguous/missing evidence preserves the detector decision. No automatic confirmations or training labels are created. Existing confirmed crops remain protected by publishing logic. DINO features are reused, with no added neural inference per animal detection.

Validation: 105 regression tests, including real video pipeline suppression and cat correction. Historical reserved-clip check retained all 71 correctly labeled animal crops and suppressed 29 of 38 person crops presented as Dog. Development validation is not an independent prospective accuracy estimate.

Dan supplied clips 20012, 20013, 23038, 23046, 23123, 23227, 23228. The saved crops confirm people labeled animals and cats labeled Dog. Strong corrections cover five of six cat crops, including all three in 23227; the small rear-view cat in 23038 remains below the gate. Some person errors also remain below the gate. Do not claim all species errors resolved. The earlier Duke suggestion on detection 2894 in 23227 was erroneous and must not be treated as training evidence.


## Person-first regions (1.2.3)

Process accepted Person detections before animals. If at least 90% of an animal box is inside a same-frame Person box with detector score >=0.7, give Person priority only when the crop classifier itself favors Person: score >=0.55, margin >=0.1, two independent confirmed clips/images, second cosine >=0.45. Otherwise retain the existing conservative species check. No image region is masked simply because it overlaps a person, and no added neural inference is required. A person holding a pet can therefore retain both detections.

107 regression tests pass, including actual video pipeline tests for Person-first ordering and preservation of a fully overlapping Dog with Dog features. Pessimistic held-clip validation assumes Person overlap for every animal: all 71 correctly labeled animals retained; 36 of 38 person crops presented as Dog suppressed under that assumed context. Actual suppression also requires the same-frame geometry guard; this is development validation, not prospective accuracy.


## Unknown Type abstention (1.2.4)

Uncertain animal proposals now retain the crop as subject_type Unknown rather than falling back to Dog/Cat. Accept the detector species only when the confirmed-Type head ranks that same species first with score >=0.65, margin >=0.2 and two independent clip/image supports with second cosine >=0.45. Strong alternative species corrections retain the 0.8/0.3/0.5 gate. Missing features or insufficient references also abstain. Existing strong Person suppression and Person-region checks remain unchanged.

Unknown Type never receives a named identity or candidate identity list, never creates a training reference, and remains reviewable and manually correctable. This is distinct from an unknown individual's name within a known species and from Unknown Motion / No Visible Subject. Add Unknown Type with an idempotent migration preserving rows, foreign-key links, indexes and notification triggers. Windows 1.1.33 displays Type Unknown and offers Unknown in review/crop Type choices. Existing human-confirmed crops are preserved.

Dan reviewed the two remaining crops in 20012 as Trish's hair/back of head from a high angle, with insufficient visible evidence to establish a Type from those crops. The intended automated result is unconfirmed Type Unknown, with the images retained.


## Repeated-view species safeguard (1.2.5)

After frame-level Type checks and tracking, accept Cat/Dog with either a supported single image (Type-head score >=0.75, gap >=0.3, second independent confirmed-reference cosine >=0.5) or two supported crop images separated by >=0.5 seconds. Supporting views must have matching model versions, quality >=0.2, distinct JPEG hashes, and independently pass the existing frame-level Type rule. Repeated copies and near-time frames cannot supply two supports. A strong conflicting Person/Cat/Dog view makes the track Type Unknown, even if its primary image is strong. Unknown crops remain reviewable and receive no named identity or training label.

This reuses already computed DINO features. There are no model downloads or additional neural inferences. Threshold scores are not probabilities. An unfamiliar species can still resemble a known species across multiple frames; this safeguard cannot guarantee wildlife rejection without representative reviewed examples.

115 regressions pass, including a real video pipeline fixture showing that a borderline one-view animal remains visible as Type Unknown, plus single strong-image acceptance, time separation, duplicate protection, bad/mismatched evidence, and conflicting views. Historical evaluation is development evidence, not independent prospective accuracy.


## Track continuity and species evidence retention (1.2.6)

Keep uncertain animal frames in the detector species' spatial track until the final species check, rather than splitting Cat/Dog and Unknown appearances before combining evidence. Missing or conflicting support still makes the final track Unknown. Existing same-frame Person suppression, species thresholds, independent-reference requirements, crop quality gates and identity safeguards are unchanged.

Within the existing four-view budget, retain supported Cat/Dog views ahead of sharper uncertain views. Preserve evidence of a different supported species even when its crop quality is lower, so a strong conflict can still veto the final species. Repeated JPEGs and insufficiently separated views cannot supply multiple supports. These are temporary inference views, not new confirmed reference labels.

Dan corrected the original confirmed Dog answer in replay clip 23318: probably Gus (Cat), uncertain; Unknown is appropriate. Clip 23392 is an unclear white animal, with Cat and Kushka inferred from context. Clip 23257 has clear Stubby/Dog crops; Dan also identified a newly detected dark animal farther down the path as Dog. Clip 23401 contains Lola/Cat.

An experimental nine-tile closer search increased processing cost, still missed Lola in 23401, and labeled the dark Dog in 23257 Cat. That detector change is rejected and is not part of this release. Small/distant cat detection remains unresolved. The release only changes track continuity and evidence retention, with no added neural inference or threshold relaxation. Replays are development evidence, not an independent accuracy score.
