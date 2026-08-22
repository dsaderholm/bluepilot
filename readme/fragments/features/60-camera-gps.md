### Giving the forward camera the GPS its own car withholds

**Ford-specific, and unproven on the road at the time of writing.** The defect below is measured; the
fix has never been driven. Whether the camera actually starts reading signs once it has the data is
the open question, and it is the whole reason the feature exists.

Ford's traffic sign recognition runs on the forward camera, and on this car it has never reported a
single speed limit. Not a wrong one — none. The signal that carries it, `TsrVLim1MsgTxt`, reads the
"no data" sentinel on every frame of every drive ever recorded here, including a 27-segment highway
run at 72 mph past dozens of posted signs.

The camera is not broken and does not think it is in an unsupported country. Asked directly, it
reports traffic sign recognition **available**, in **mph**, with no region complaint — it has
dedicated fault codes for "country not supported" and "region not supported" and emits neither. What
it does report, continuously, is `U0253 — Lost Communication With Accessory Protocol Interface
Module`, additional fault symptom **Missing Message**.

That fault turns out to be literally true. The camera is a listed receiver of three GPS messages
from the SYNC module, and across a full drive:

| message | contents | frames |
|---|---|---|
| `0x462` `APIMGPS_Data_Nav_1` | latitude, longitude | **3494** |
| `0x463` `APIMGPS_Data_Nav_2` | UTC date and time, position accuracy, compass, GPS fault flag | **0** |
| `0x464` `APIMGPS_Data_Nav_3` | heading, altitude, satellites, speed, accuracy | **0** |

One of three arrives. The camera spends every drive waiting on two messages that are never
transmitted, never leaves its "no navigation data" state, and never enters the fused mode that other
owners report as the state in which signs are actually read.

None of that is map data — there is no map speed limit anywhere in those messages. It is plain GPS
telemetry, of the kind any navigation-equipped SYNC broadcasts whether or not a destination has ever
been entered. Why this car withholds two of the three is not yet known.

**So openpilot sends them itself.** The comma has its own GPS receiver, and every field those two
messages carry — time, heading, altitude, satellite count, accuracy — is already in it. The two
messages are synthesized and placed on the camera's bus at 1 Hz, the same rate the car sends the
message it does send.

Three properties worth stating, because this writes to a bus:

- **It commands nothing.** These messages carry position, time and heading. There is no actuator
  field in either of them; they cannot influence steering, throttle or braking, and the vehicle side
  of the bus is untouched.
- **It stands down on its own.** The car is watched for the real messages, and one received frame
  disables the synthesizer for the rest of the drive. openpilot never competes with a working SYNC.
- **It cannot take the car off the road.** The whole path is latched off on any failure, and a
  missing attribute disables the feature rather than the car — a rule this fork learned the hard way
  when a follow-distance convenience once made the car undrivable.

One approximation is deliberate and flagged: the comma reports position accuracy in metres, while
Ford's signals want dilution of precision, which is satellite geometry the comma does not expose.
The conversion is a documented estimate and is the only part of the mapping that is not a direct
measurement.

Setting is **Send GPS To The Camera**, and it ships on.
