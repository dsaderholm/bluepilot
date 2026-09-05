import os
from openpilot.common.basedir import BASEDIR

MAPD_BIN_DIR = os.path.join(BASEDIR, 'third_party/mapd_pfeiferj')
MAPD_PATH = os.path.join(MAPD_BIN_DIR, 'mapd')

# FusionPilot: mapd v2, vendored beside v1 rather than replacing it, so both can run during the
# cutover -- v2 publishing mapdOut for comparison while v1 still feeds Speed Limit Assist. Which
# one SLA actually reads is the MapdV2 param; this is only which binaries exist.
#
# Vendored rather than downloaded because that is how v1 arrives: the binary is committed and the
# device gets it with the update, so no boot-time download and no network needed for a test drive.
MAPD_V2_PATH = os.path.join(MAPD_BIN_DIR, 'mapd_v2')
MAPD_V2_VERSION = "v2.3.1"

# The three states of the MapdV2 param. Named because `== 2` at a call site says nothing about why,
# and because the middle one is the state nobody expects: v2 running and logged, SLA still on v1.
MAPD_V2_OFF = 0
MAPD_V2_OBSERVE = 1
MAPD_V2_ON = 2
