# AI Identity Recognition — BlinkDRS 1.1.0

October 3, 2026. Local analysis and recognition suggestions; not the completed 2.0 milestone.

## Use

Recorded Clips has an Identities column. Click it, or click the identities under
Playback's title. Analyze Clip queues an existing recording; new recordings queue
automatically while the worker is enabled. This includes Live recordings after
publication to the local catalog. The initial deployment does not analyze the
entire historical archive automatically.

Select a detected label to see its cropped image and sampled appearance times.
Create a Person/Cat/Dog identity, then Confirm / Change that image's label. A usable
confirmed image becomes a model-specific reference for future clips. Unknown,
changed identity, and removed labels invalidate their old training references.
Changing subject type rebuilds a usable reference in the worker. Whole-clip manual
labels alone cannot provide training images. People require exactly one usable
face in the detected subject crop; a rear view or small/blurred face stays Unknown.
Pet matching uses image appearance features and is experimental.

## Processing and correctness

A separate process runs one clip at a time with one Torch/OpenCV thread, lower
priority, and optional Live View pause checkpoints. The installed service pauses
before starting clips and between sampled frames while Live View is active or the
controller cannot be reached. Sample at most 16 frames across a clip. Overlapping
image views help retain smaller pet detail; duplicate crops are suppressed.
Track times are sampled observations, not exact arrival/departure guarantees.
Short appearances may be missed. No subjects found means none in analyzed samples.

Subject detection scores are separate from identity cosine similarity. Similarity
is not a probability. Face/pet suggestion thresholds (.50/.90) and .05 separation
from the next identity are initial conservative settings, not calibrated accuracy
claims. Confirmed identities are authoritative. Reanalysis replaces unconfirmed AI
results atomically, retains confirmed rows, and suppresses removed detection keys
for the same deterministic analysis. Changed model/sampling code may produce new
keys; revisit suppressions when upgrading the pipeline.

Identity, detection, sample, evidence, rejection, job, and worker-status records
are additive SQLite tables. Cropped JPEGs and embeddings stay local. Successful
analysis verifies the MP4 content hash before publishing. Invalid/mutating clips
report failure. The kernel lock prevents concurrent inference workers. Worker
interruption marks active jobs retryable without losing confirmed labels. New
clip queue triggers are installed when the worker runs with --new-clips.

## Runtime and sources

Actual device: Raspberry Pi 4 Model B Rev 1.5, 64-bit OS, 8 GB RAM (7819 MiB usable),
Python 3.13.5. A separate .venv-ai keeps inference dependencies out of DVR's .venv.
Torch 2.8.0+cpu, torchvision 0.23.0, transformers 4.57.6, OpenCV headless 4.14.0.94,
Pillow 12.3.0. See requirements-ai.txt and deploy/blink-identity.service.
Prepare weights explicitly with identity_worker.py --models ai-models
--prepare-models. Inference loads only local weights; deployed service sets offline
flags. Downloads obtain model files, never send camera clips or crops outside.

- Detector: [torchvision SSDLite MobileNet V3 COCO weights](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.detection.ssdlite320_mobilenet_v3_large.html).
- Pet features: [DINOv2 small model card](https://huggingface.co/facebook/dinov2-small). General image features, not a pet identity model tuned for these cameras.
- Faces: [OpenCV YuNet/SFace](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html).
- CPU wheels: [official PyTorch index](https://download.pytorch.org/whl/cpu/).

Model weight hashes plus preprocessing identifiers distinguish reference galleries.
Initial package-index resolution selected unwanted GPU dependencies and exhausted
RAM-backed /tmp; verified task-only abandoned wheels were removed. The successful
installation used pinned CPU wheels and disk-backed temporary storage. DVR stayed
active. No change to its Python environment was required.

## Validation

13 distinct database/API/regression tests pass; Windows Release build has no
warnings/errors. Real Pi tests used a backup catalog and three existing recordings.
The first full-frame configuration processed each in about 16 seconds and peaked
at 508 MiB; later overlapping views trade more CPU time for pet detail. Catio
produced actual person crops, without a usable face; no identity was invented.
Recent sampled camera frames without detected pets do not establish pet accuracy.
Public COCO two-cat and OpenCV face images validated two-cat deduplication, a
384-dimensional pet embedding and a 128-dimensional face embedding. These public
examples were kept only in ignored staging and did not create production identities.

Still required: Dan's visual acceptance and usable reference enrollment; daytime,
infrared, small-subject and multiple-subject accuracy; sustained backlog and resource
measurements alongside real Live View/recording. This is an experimental 1.1.0
feature, not a claim of reliable individual recognition from arbitrary footage.

## Deployment checkpoint

Deployment completed on October 3, 2026 after Dan provisioned a fresh Blink login.
Restarting the idle controller loaded that login: all 13 cameras were discovered,
cloud polling succeeded, and the local Dashboard API recovered. Provisioning alone
does not reload the running controller's credentials; the existing Settings success
message instructs the user to restart the BlinkDRS service.

The integration preserves the Pi's prior controller changes. Original files and
catalog backups remain in .ai-backup-20261003 and .ai-backup-20261003b. Both
blink-dvr.service and blink-identity.service are active; the latter is enabled at
boot, uses its separate CPU environment, and loaded its local models successfully.
The identity API is available against the production catalog. No test identity
profiles were created. Visual acceptance and reference enrollment remain required.
The tested Windows 1.1.0 package is in this chat's outputs/BlinkDRS-AI directory.

## Unknown motion review (1.1.2)

An empty completed subject result appears as Unknown Motion / No Visible Subject,
unconfirmed because only sampled frames were checked. Mark No Visible Subject saves
a human review in clip_unknown_motion, retains the recording, and suppresses new AI
subject suggestions during reanalysis. Remove Label clears it; adding a real subject
clears it automatically. Existing subject labels must be removed before marking an
empty scene. The trigger cause remains unknown; wind is never assumed by the model.
This category does not create an identity profile or recognition references.

## Known identity subject previews (1.1.3)

Known identity selections prefer the named subject's linked image in this clip.
A whole-clip manual name has no subject bounds. The editor offers detected images
of the same type as candidates, excluding another confirmed identity, and requires
explicit confirmation before linking. Assigning a detected subject removes duplicate
whole-clip labels for the same name atomically and retains other names and evidence.
If no suitable detection exists, Analyze Clip is required; no image is invented.

## Manual crops and sensitivity check (1.1.5)

Playback Manual Crop pauses the clip, reads its frame from the Pi using FFmpeg,
and maps a drawn rectangle from a letterboxed preview to source pixel coordinates.
The controller validates archived MP4 containment, finite time and duration, source
dimensions, and rectangle bounds before saving JPEG evidence with the frame time.
Manual crops stay confirmed through reanalysis. Named crops use manual-pending until
the low-priority worker embeds them; usable crops become model-specific references.
Unknown crops have no recognition sample until the human assigns a valid identity.

Diagnostic tests at 25/50/75 percent of clips 20039, 20041, and 20032 compared current
Person 0.65 / pet 0.55 thresholds against 0.45 / 0.30. The missed cats in the first
two clips were not detected. Nine tighter 40-percent views of their midpoint frames
also found none. Diagnostic frames visibly contain small cats. No threshold change
was deployed; manual evidence addresses those failures without invented detections.
Stronger small-subject detection remains future work. Blink camera motion-trigger
sensitivity is separate from this post-recording subject detector.

## Release confirmation

Dan reported the deployed manual crop workflow working on October 3, 2026 and
authorized committing and pushing both repositories. The Windows release remains
1.1.5. All requested functionality is deployed; continued user testing, recognition
accuracy, and sustained workload measurements remain open.
