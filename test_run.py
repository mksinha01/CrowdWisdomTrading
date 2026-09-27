from cwt.video.hyperframes import HyperFramesBackend
from cwt.video.openmontage import OpenMontageBackend

for b in (HyperFramesBackend(), OpenMontageBackend()):
    a = b.available()
    print(b.name, a.available, '|', a.reason)

from cwt.config import Settings
from cwt.video.backend import build_chain
print([b.name for b in build_chain(Settings.from_env())])
