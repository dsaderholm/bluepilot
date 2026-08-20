// =============================================================================
// rear_radar_mount.scad -- parametric rear-facing radar mount, 2020 Ford Fusion
//
// FusionPilot / BluePilot.  Companion to BP-REAR-RADAR-MOUNT.md and
// BP-REAR-RADAR-PLAN.md section 2.
//
// THIS IS A FIRST PASS, NOT A PART.  Nothing below has been measured on the
// car.  Every number carrying a "MEASURE:" comment is a placeholder that will
// be wrong.  The geometry is arranged so that filling those in is the whole
// job; the topology does not change.
//
// It implements the CHOSEN layout: radar LOW, behind the unpainted textured
// black lower valance, hung off the rear bumper reinforcement beam.  The
// fallback (external, below the bumper, at licence-plate height) is the same
// cradle, shim and yoke with a different leg -- set part = "leg_drop" and add
// the hood.  It exists only for the case where the depth measurement kills the
// behind-the-valance position.
//
// The low position was chosen to dodge metallic paint attenuation.  That is
// the right reason and it is worth restating what it depends on: the valance
// being UNPAINTED textured polypropylene, which has no aluminium flake in it.
// UNVERIFIED on this car -- see the brief's M0.
//
// THE ONE THING THIS PART EXISTS TO DO
// ------------------------------------
// ESR.dbc has no elevation correction.  ANGLE_MOUNTING_OFFSET and
// ANGLE_MISALIGNMENT are azimuth only.  Azimuth is trimmable over CAN by
// +/-8 deg; elevation is trimmable by nothing at all.  So the elevation joint
// is a wedge shim with a printed angle, and the azimuth joint is a coarse arc
// slot that only has to land inside the CAN correction range.
//
// COORDINATES
//   +X  vehicle right (as seen from behind the car, looking forward)
//   +Y  vehicle REARWARD -- this is the radar boresight
//   +Z  up
//   origin: center of the yoke's front face, at the elevation joint
//
// UNITS: millimetres, as every printer and every caliper wants.  The brief
// carries the same numbers in inches for tape-measure work.
//
// SI internally, US customary in anything the owner reads -- so this file is
// mm and the markdown is both.
// =============================================================================


// -----------------------------------------------------------------------------
// [0] WHAT TO RENDER
// -----------------------------------------------------------------------------
// "assembly"  -- everything, for checking it does not intersect itself
// "cradle"    -- the radar shell            print 1
// "strap"     -- top clamp + alignment refs print 1
// "shim"      -- one wedge at shim_angle_deg
// "shim_set"  -- the whole family, laid out flat for one print
// "yoke"      -- elevation-to-azimuth adapter
// "leg_beam"  -- CONCEPT A car-side leg (beam mount)
// "leg_drop"  -- CONCEPT B car-side leg (external drop)
// "hood"      -- CONCEPT B splash hood
part = "assembly";

$fn = 48;


// -----------------------------------------------------------------------------
// [1] THE RADAR.  Datasheet numbers -- CONFIRM WITH CALIPERS ON THE PART.
// -----------------------------------------------------------------------------
// BP-REAR-RADAR-PLAN.md section 1: 173.7 x 90.2 x 49.2 mm "including mounting
// features" (Delphi datasheet, Delphi ESR 2.5).  "Including mounting features"
// is doing unknown work in that sentence: it may mean the bare brick is
// smaller and the quoted envelope already contains ears we cannot see.
//
// MEASURE: all three, on the module, with calipers, before printing anything
// that is not a fit check.
radar_w = 173.7;   // X, lateral
radar_h =  90.2;   // Z, vertical
radar_d =  49.2;   // Y, front-to-back (antenna face to rear of module)

// Clearance around the module inside the cradle.  0.5-0.8 mm suits a printer
// holding +/-0.2.  A foam gasket takes up the rest -- see radar_gasket_t.
radar_fit = 0.6;

// Compressible gasket between the module and the cradle floor/back, so the
// strap preloads the module rather than the print tolerance doing it.
// Closed-cell EPDM foam tape, NOT open-cell: open cell holds water.
radar_gasket_t = 2.0;

// The module's OWN mounting features: UNKNOWN.  The datasheet envelope says
// they exist; nothing in this repo says where.
// MEASURE: with the module in hand -- are they ears with through-holes, blind
// threaded bosses, or a slide rail?  If they are through-holes, fill in the
// pattern and set radar_has_bolt_pattern = true, and the cradle stops relying
// on the clamp alone.
radar_has_bolt_pattern = false;
radar_bolt_dx = 150;   // MEASURE: hole spacing in X
radar_bolt_dz =  70;   // MEASURE: hole spacing in Z
radar_bolt_d  =   5.4; // MEASURE: hole diameter (5.4 = clearance for M5)

// The connector.  WHICH FACE IT EXITS DECIDES WHETHER CONCEPT A IS POSSIBLE.
// If it exits the REAR face, its backshell plus the harness bend radius adds
// straight onto the depth budget, and the depth budget is the binding
// constraint in the whole project.
//
// MEASURE: face, position, mated backshell depth, minimum harness bend.
// Values: 0 = rear face, 1 = bottom face, 2 = left face, 3 = right face.
connector_face      = 1;    // MEASURE.  1 (bottom) is what we WANT, not what we know.
connector_w         = 40;   // MEASURE
connector_h         = 25;   // MEASURE
connector_stickout  = 55;   // MEASURE: mated backshell + 40 mm of bend relief
connector_offset_x  = 0;    // MEASURE: from module centerline


// -----------------------------------------------------------------------------
// [2] THE CAR.  ALL OF THIS IS UNVERIFIED.  See the brief's measurement list.
// -----------------------------------------------------------------------------
// M1 in the brief, and the number that decides the layout.  Inside face of the
// lower valance skin, straight forward to the first hard structure.
// MEASURE: this one first.  Below about 85 mm the behind-the-valance position
// is dead and the fallback leg is the only option.
cover_clear_depth = 110;

// The valance itself, now that it is the radome.
// MEASURE: thickness with calipers at a trimmed edge or through an existing
// sensor aperture.  MEASURE: rib depth by feel -- reach up behind the intended
// spot and find how far the internal ribbing stands proud of the skin.
// Both UNVERIFIED.  Typical injection-moulded bumper skin is 2.5-3.5 mm and
// ribs stand 8-20 mm proud, but this car has not been checked.
valance_t         = 3.0;   // MEASURE
valance_rib_depth = 12;    // MEASURE

// Air gap between the antenna face and the inner face of the valance skin.
// The cradle's front lips are relieved so they land on the RIBS, never on the
// skin: the skin flexes, and anything clamped to it moves the aim.
// The right value is a bench question, not a CAD question.  Two schools:
// keep it large enough that the reflection diffuses (>25 mm), or tune it to a
// multiple of a half wavelength in air (1.96 mm at 76.5 GHz).  UNVERIFIED
// which one this module wants.  Bench-test with a scrap of the actual valance.
face_standoff = 25;

// Height of the intended antenna face center above ground, car on LEVEL
// ground.  Delphi manual window is 300-860 mm.  The low mount pushes toward
// the bottom of that window on purpose, so this is the number with the least
// margin in the whole design.
// MEASURE: unloaded AND with the trunk loaded.  See rear_squat below.
target_face_height = 380;   // MEASURE

// How much the rear ride height drops between empty and loaded.
// This matters twice.  It subtracts from target_face_height, and -- because
// the body pitches nose-up when the rear squats -- it aims a rear-facing
// radar DOWNWARD.  Wheelbase is 2850 mm (FORD_FUSION_MK5 CarSpecs).
// MEASURE: ground to the rear wheel arch lip, empty, then with the trunk
// loaded the way it is on a road trip.
rear_squat = 25;            // MEASURE

// Pitch of the surface you intend to bolt to, relative to true level, with
// the car on level ground.  Positive = the surface's outward normal points
// DOWN.  This plus radar_face_squareness is what sets the shim.
// MEASURE: phone clinometer on the beam's rear face, zeroed against a spirit
// level on the rocker or door sill so the ground's slope cancels.
mount_surface_pitch = 0;

// The module's own antenna face may not be square to its mounting features.
// MEASURE: level on the radar's face vs level on its mounting datum, on the
// bench, before it ever goes near the car.  This is a real error source that
// is invisible once the thing is bolted up.
radar_face_squareness = 0;

// Clear lateral width available at the intended depth, between parking sensor
// cones / exhaust hangers / the tow eye socket.
// MEASURE.  The cradle needs about 190 mm.
available_width = 220;


// -----------------------------------------------------------------------------
// [3] THE ELEVATION JOINT -- the part with no CAN fallback
// -----------------------------------------------------------------------------
// Positive shim_angle_deg pitches the BORESIGHT DOWN.
// Geometric check, because the sign is easy to get backwards:
//   a wedge thicker at the TOP pushes the top of the cradle rearward,
//   the cradle leans forward, the face tips DOWN.  Thicker at top = nose down.
//
// Set this to
//     -(mount_surface_pitch + radar_face_squareness) - squat_bias/2
// once all three are measured, then round to the nearest printed shim.
//
// The squat term is why this is not just "make it level".  Loading the trunk
// pitches the body nose-up, which aims a REAR-facing radar down by
// atan(rear_squat / wheelbase).  Biasing the unloaded aim slightly UP splits
// that error instead of paying all of it when the car is loaded -- which is
// exactly when the road trip happens.
shim_angle_deg = 0;

// The family to print.  0.25 deg steps are finer than the car can be measured,
// which is the right way round: the shim should never be the limiting error.
shim_set_angles = [-3, -2, -1.5, -1, -0.5, -0.25, 0, 0.25, 0.5, 1, 1.5, 2, 3];

shim_base_t = 6;     // thickness at mid-height, at 0 deg
shim_w      = 160;   // outline width
shim_h      =  80;   // outline height

// Interface bolt pattern, shared by cradle back plate / shim / yoke.
// M6 through-bolts with washers both sides and nyloc nuts.  Not heat-set
// inserts: this joint is loaded in TENSION across print layers, which is the
// one direction a printed part is weak in.  A fastener backing out here is an
// elevation change nothing on the bus will tell you about.
ifc_bolt_dx = 60;    // +/- in X
ifc_bolt_dz = 28;    // +/- in Z
ifc_bolt_d  = 6.6;   // M6 clearance


// -----------------------------------------------------------------------------
// [4] THE AZIMUTH JOINT -- coarse only, CAN trims the rest
// -----------------------------------------------------------------------------
// Only has to get inside +/-8 deg for the CAN offset to finish the job.  But
// aim still matters more than that tolerance suggests: at 50 m, 3 deg is
// 2.6 m of lateral error, which is more than half a lane.  Coarse-set it by
// eye off the sight posts, then trim over CAN and verify by parking behind a
// known target.
az_range_deg  = 8;    // half-range of the arc slots
az_bolt_r     = 50;   // bolt circle radius on the horizontal interface
az_bolt_d     = 6.6;  // M6 clearance
az_bolt_n     = 3;    // three points, so the joint cannot rock
az_pad_r      = 62;   // radius of the LEG's pad
az_dial_r     = 78;   // radius of the YOKE's pad -- bigger, so its rim is the dial
az_pilot_d    = 12;   // center pilot boss: takes shear, forces pure rotation
az_pilot_h    = 5;


// -----------------------------------------------------------------------------
// [5] STRUCTURE
// -----------------------------------------------------------------------------
wall        = 6;    // cradle side/bottom wall
back_t      = 8;    // cradle back plate
strap_t     = 8;    // top clamp plate
yoke_t      = 10;   // yoke vertical plate
yoke_pad_t  = 10;   // yoke horizontal pad
leg_t       = 10;   // leg plate

// Alignment references, printed in place.  See the brief.
sight_post_d    = 8;
sight_post_h    = 22;
sight_post_span = 70;   // Y separation between the two posts
level_pad_w     = 90;   // flat pad on the strap for a digital angle gauge
level_pad_d     = 60;

drain_slot_w  = 4;
drain_slot_n  = 7;

// CONCEPT A leg -- reaches from the yoke pad up/forward to the beam.
// ALL FOUR ARE GUESSES.  There are no factory radar mounting points on the
// rear of this car; the beam is the first thing to measure and it may have
// nothing usable on it at all.
leg_rise      = 60;   // MEASURE: yoke pad up to the beam's lower face
leg_reach     = 70;   // MEASURE: yoke pad forward to the beam's rear face
leg_pad_w     = 120;  // MEASURE: flat width available on the beam
leg_pad_h     =  50;  // MEASURE: flat height available on the beam
leg_slot_dx   =  40;  // MEASURE: whatever hole spacing the beam actually has
leg_slot_len  =  20;  // slop, because the beam's holes will not be where you want
leg_slot_d    =   8.6;// M8 clearance

// CONCEPT B extras
drop_length   = 140;  // MEASURE: below the valance to the target face height
hood_overhang =  35;  // splash / stone-chip hood projection


// =============================================================================
// DERIVED
// =============================================================================
cav_w = radar_w + 2*radar_fit;
cav_h = radar_h + 2*radar_fit;
cav_d = radar_d + radar_fit;

ear_w = 8;                               // outboard strap-bolt ears
shell_w = cav_w + 2*wall;                // the shell itself
cradle_w = shell_w + 2*ear_w;            // overall envelope, ears included
cradle_h = cav_h + wall;                 // open top; the strap closes it
cradle_d = back_t + cav_d;
strap_bolt_x = cav_w/2 + wall + ear_w/2;

yoke_w = shim_w;
yoke_h = shim_h + 20;
az_center_y = -yoke_t - az_pad_r + 20;   // center of the azimuth joint

wheelbase = 2850;   // FORD_FUSION_MK5 CarSpecs, via BP-REAR-RADAR-PLAN.md section 4

// Body pitch introduced by loading the rear, in degrees, and the direction it
// aims the boresight.  Positive = boresight aimed DOWN when loaded.
squat_bias = atan(rear_squat / wheelbase);

// Where the boresight meets the road, at the nominal aim.  This is the whole
// argument for the shim: at this mount height a degree of error is not
// cosmetic.  1 deg down at 380 mm puts the boresight on the tarmac at 22 m.
road_intercept = (shim_angle_deg > 0)
                 ? target_face_height / tan(shim_angle_deg) / 1000
                 : 0;

// Depth actually consumed behind the valance skin.
depth_used = face_standoff + cradle_d + shim_base_t + yoke_t
             + (connector_face == 0 ? connector_stickout : 0);

loaded_height = target_face_height - rear_squat;

echo(str("depth consumed behind the valance skin: ", depth_used, " mm"));
echo(str("measured clear depth: ", cover_clear_depth, " mm"));
echo(str("squat bias: ", squat_bias, " deg boresight-down when loaded"));
echo(str("face height loaded: ", loaded_height, " mm"));
if (road_intercept > 0)
  echo(str("nominal boresight meets the road at ", road_intercept, " m"));

if (depth_used > cover_clear_depth)
  echo("WARNING: does not fit behind the valance. Use leg_drop, the external fallback.");
if (cradle_w > available_width)
  echo("WARNING: cradle is wider than the clear lateral width you measured.");
if (target_face_height < 300 || target_face_height > 860)
  echo("WARNING: outside the Delphi 300-860 mm mount height window, UNLOADED.");
if (loaded_height < 300)
  echo("WARNING: drops BELOW the 300 mm floor when the trunk is loaded. Raise it.");
if (face_standoff < valance_rib_depth)
  echo("WARNING: face_standoff is inside the rib depth -- the cradle will foul the ribs.");


// =============================================================================
// HELPERS
// =============================================================================

// An arc-shaped slot, swept about Z at radius r, +/- ang degrees, width w.
module arc_slot(r, ang, w, h) {
  n = max(2, ceil(ang));
  for (i = [0 : n-1])
    hull() {
      rotate([0, 0, -ang + 2*ang*i/n])
        translate([r, 0, 0]) cylinder(d = w, h = h, center = true);
      rotate([0, 0, -ang + 2*ang*(i+1)/n])
        translate([r, 0, 0]) cylinder(d = w, h = h, center = true);
    }
}

// The radar as a solid, for subtracting and for the assembly view.
// Origin at the center of its REAR face, boresight +Y.
module radar_block() {
  color("dimgray")
    translate([-radar_w/2, 0, -radar_h/2])
      cube([radar_w, radar_d, radar_h]);

  // connector stub, so the assembly view shows whether it fouls anything
  if (connector_face == 0)
    translate([connector_offset_x - connector_w/2, -connector_stickout, -connector_h/2])
      color("black") cube([connector_w, connector_stickout, connector_h]);
  if (connector_face == 1)
    translate([connector_offset_x - connector_w/2, radar_d/2 - connector_h/2,
               -radar_h/2 - connector_stickout])
      color("black") cube([connector_w, connector_h, connector_stickout]);
  if (connector_face == 2)
    translate([-radar_w/2 - connector_stickout, radar_d/2 - connector_h/2,
               -connector_w/2])
      color("black") cube([connector_stickout, connector_h, connector_w]);
  if (connector_face == 3)
    translate([radar_w/2, radar_d/2 - connector_h/2, -connector_w/2])
      color("black") cube([connector_stickout, connector_h, connector_w]);
}

// Shared interface bolt pattern, as through-holes along Y.
module ifc_holes(len) {
  for (sx = [-1, 1], sz = [-1, 1])
    translate([sx*ifc_bolt_dx, -1, sz*ifc_bolt_dz])
      rotate([-90, 0, 0]) cylinder(d = ifc_bolt_d, h = len + 2);
}


// =============================================================================
// CRADLE -- the radar shell
//
// PRINT ORIENTATION: standing on its bottom shelf, exactly as drawn.  Layers
// then stack in Z.  The load is the module's mass cantilevered rearward, which
// is a moment about X carried by the two side walls; in this orientation that
// is an IN-PLANE load on the side walls, not a peel across layers.  Printed
// lying on its back plate it would be a pure interlayer tensile load and it
// would delaminate on a washboard road.
//
// 5 perimeters minimum, 40% gyroid.  Perimeters carry this, not infill.
// =============================================================================
module cradle() {
  difference() {
    union() {
      // back plate + side walls + bottom shelf
      translate([-shell_w/2, 0, -cav_h/2 - wall])
        cube([shell_w, cradle_d, cradle_h]);

      // outboard ears at the top of each side wall, so the strap bolts have
      // material to pass through and the strap still seats on a flat face
      for (sx = [-1, 1])
        translate([sx > 0 ? (cav_w/2 + wall) : -(cav_w/2 + wall + ear_w),
                   back_t, cav_h/2 - 34])
          cube([ear_w, cav_d, 34]);

      // RIB STANDOFF PADS.  Four stubs at the outer corners, projecting to
      // just short of the valance's internal ribbing.  They are RATTLE
      // SPACERS, not a load path: the cradle hangs off the beam, and the
      // valance skin flexes with every gust and every car wash.  Cap each with
      // closed-cell foam tape so nothing rigid ever ties the aim to the skin.
      // Nothing here crosses the antenna aperture -- they sit outboard of the
      // module's own width.
      for (sx = [-1, 1], sz = [-1, 1])
        translate([sx*(cav_w/2 + wall/2), cradle_d,
                   sz*(cav_h/2 - 12) - (sz < 0 ? wall : 0)])
          rotate([-90, 0, 0])
            cylinder(d = wall*1.8, h = max(0.1, face_standoff - valance_rib_depth - 2));
    }

    // radar cavity, open to the front and to the top
    translate([-cav_w/2, back_t, -cav_h/2])
      cube([cav_w, cav_d + 1, cav_h + 50]);

    // interface bolts through the back plate
    ifc_holes(back_t);

    // the module's own bolt pattern, if it turns out to have one
    if (radar_has_bolt_pattern)
      for (sx = [-1, 1], sz = [-1, 1])
        translate([sx*radar_bolt_dx/2, -1, sz*radar_bolt_dz/2])
          rotate([-90, 0, 0]) cylinder(d = radar_bolt_d, h = back_t + 2);

    // strap bolt holes, through the ears
    for (sx = [-1, 1], sy = [-1, 1])
      translate([sx*strap_bolt_x, back_t + cav_d/2 + sy*cav_d/4, cav_h/2 - 36])
        cylinder(d = 5.4, h = 40);

    // DRAIN SLOTS.  A cradle that holds water is a cradle that freezes, and
    // this car spends its winters in Utah.  Slots run front-to-back so they
    // drain whatever the car's pitch is.
    for (i = [0 : drain_slot_n - 1])
      translate([-cav_w/2 + (i + 0.5)*cav_w/drain_slot_n - drain_slot_w/2,
                 back_t, -cav_h/2 - wall - 1])
        cube([drain_slot_w, cav_d, wall + 2]);

    // connector relief through the bottom shelf
    if (connector_face == 1)
      translate([connector_offset_x - connector_w/2 - 2,
                 back_t + radar_d/2 - connector_h/2 - 2,
                 -cav_h/2 - wall - 1])
        cube([connector_w + 4, connector_h + 4, wall + 2]);

    // connector relief through a side wall
    if (connector_face == 2 || connector_face == 3)
      translate([connector_face == 2 ? -shell_w/2 - 1 : cav_w/2,
                 back_t + radar_d/2 - connector_h/2 - 2,
                 -connector_w/2 - 2])
        cube([wall + 2, connector_h + 4, connector_w + 4]);

    // ALIGNMENT REFERENCE 1: a centerline V-notch cut into the bottom shelf at
    // x = 0.  Drop a plumb line from the car's centerline, or run a tape from
    // two symmetric points on the body, and it lands here.  The strap carries
    // the matching mark on top.
    translate([0, -1, -(cav_h/2 + wall)])
      rotate([-90, 0, 0]) cylinder(d = 8, h = cradle_d + 2, $fn = 4);
  }
}


// =============================================================================
// STRAP -- top clamp, and the part that carries the alignment references
//
// PRINT ORIENTATION: flat on the bed, level pad DOWN against the glass.  The
// level pad is a datum; a bed-side surface is the flattest thing a printer
// makes.  Do not print it pad-up and rely on ironing.
// =============================================================================
module strap() {
  strap_w = cradle_w;
  strap_d = cav_d;
  // A longer sighting baseline is a more accurate one, but the posts have to
  // land on the plate.  Nothing may hang out over the antenna aperture.
  post_span = min(sight_post_span, strap_d - sight_post_d - 8);

  difference() {
    union() {
      translate([-strap_w/2, back_t, 0]) cube([strap_w, strap_d, strap_t]);

      // ALIGNMENT REFERENCE 2: the level pad.  Flat, and parallel to the
      // radar's antenna face normal in the vertical sense -- so a digital
      // angle gauge laid on it reads the boresight elevation DIRECTLY, with
      // the car on level ground.  This is the only readout you get: nothing
      // on the CAN bus will ever tell you the elevation is wrong.
      translate([-level_pad_w/2, back_t + strap_d/2 - level_pad_d/2, strap_t])
        cube([level_pad_w, level_pad_d, 1.2]);

      // ALIGNMENT REFERENCE 3: two sight posts on the boresight centerline,
      // equal height, V-notched.  Sight along them, or stretch a string.  The
      // line they define is the boresight in plan view -- measure from it to
      // two symmetric points on the car to coarse-set azimuth before you ever
      // touch ANGLE_MOUNTING_OFFSET.
      for (sy = [-1, 1])
        translate([0, back_t + strap_d/2 + sy*post_span/2, strap_t])
          cylinder(d = sight_post_d, h = sight_post_h);
    }

    // strap bolts
    for (sx = [-1, 1], sy = [-1, 1])
      translate([sx*strap_bolt_x, back_t + cav_d/2 + sy*cav_d/4, -1])
        cylinder(d = 5.4, h = strap_t + 2);

    // V notches in the tops of the sight posts
    for (sy = [-1, 1])
      translate([0, back_t + strap_d/2 + sy*post_span/2,
                 strap_t + sight_post_h - 2])
        rotate([0, 45, 0]) cube([6, sight_post_d + 2, 6], center = true);

    // centerline witness mark on the level pad
    translate([-0.6, back_t + 2, strap_t + 0.6])
      cube([1.2, strap_d - 4, 1.2]);
  }
}


// =============================================================================
// SHIM -- the elevation wedge
//
// Positive angle = boresight DOWN (wedge thicker at the top).
//
// Resolution: over the 2 * ifc_bolt_dz = 56 mm bolt span, 1 deg is under
// 1 mm of thickness difference, so a 0.2 mm layer resolves about 0.2 deg and
// the printer is more accurate than any measurement you will make of the car.
// That is the right way round.  Print the family, fit the one that zeroes the
// gauge on the level pad.
//
// PRINT ORIENTATION: flat, thin face down.  Layers are then perpendicular to
// the bolt axis, which loads them in COMPRESSION under a clamped joint.  This
// is the one part where that orientation is correct.
// =============================================================================
module shim(a = 0) {
  t_slab = shim_base_t + (shim_h/2)*tan(abs(a)) + 1;

  difference() {
    // slab
    translate([-shim_w/2, 0, -shim_h/2]) cube([shim_w, t_slab, shim_h]);

    // wedge cut: leaves thickness t(z) = shim_base_t + z*tan(a)
    translate([0, shim_base_t, 0])
      rotate([-a, 0, 0])
        translate([0, 500, 0]) cube([1000, 1000, 1000], center = true);

    // bolt holes
    ifc_holes(t_slab);

    // Engrave the angle on the FLAT face (y = 0), which is the face that lands
    // on the bed when the shim is printed.  A drawer of near-identical wedges
    // is useless without this.
    translate([-shim_w/2 + 10, 0.8, -shim_h/2 + 6])
      rotate([90, 0, 0])
        linear_extrude(0.9)
          text(str(a, " deg"), size = 9, halign = "left", valign = "bottom");

    // Engrave UP.  Installing a wedge upside down inverts the correction and
    // doubles the error, silently, and there is no signal on the bus that
    // would ever tell you.
    translate([shim_w/2 - 20, 0.8, 0])
      rotate([90, 0, 0])
        linear_extrude(0.9)
          text("UP", size = 9, halign = "center", valign = "center");
  }
}

// The whole family, laid flat on the bed for one print.  rotate([90,0,0]) puts
// the shim's flat face down; the engraving then lands on the first layer,
// which is the crispest surface a printer makes.
module shim_set() {
  n = len(shim_set_angles);
  cols = 3;
  for (i = [0 : n - 1])
    translate([(i % cols) * (shim_w + 10), floor(i / cols) * (shim_h + 10), 0])
      rotate([90, 0, 0])
        shim(shim_set_angles[i]);
}


// =============================================================================
// YOKE -- elevation joint below, azimuth joint above
//
// PRINT ORIENTATION: on its side, so the profile of the L lies in the bed
// plane and the extrusion runs along X.  The corner of the L is where the
// whole cantilever moment goes; printed any other way that corner is an
// interlayer joint loaded in tension.
// =============================================================================
module yoke() {
  difference() {
    union() {
      // vertical plate -- the elevation interface, front face at y = 0
      translate([-yoke_w/2, -yoke_t, -yoke_h/2]) cube([yoke_w, yoke_t, yoke_h]);

      // horizontal pad -- the azimuth interface.  Larger than the leg's pad,
      // so its rim stays visible and can carry the protractor.
      translate([0, az_center_y, yoke_h/2])
        cylinder(r = az_dial_r, h = yoke_pad_t);

      // gusset, so the L is not a hinge.  Built as a hull of three edges,
      // which is unambiguous where a rotated extrusion is not.
      hull() {
        translate([-yoke_w/2, -yoke_t - 1, yoke_h/2 - 45]) cube([yoke_w, 1, 1]);
        translate([-yoke_w/2, -yoke_t - 45, yoke_h/2 - 1]) cube([yoke_w, 1, 1]);
        translate([-yoke_w/2, -yoke_t - 1, yoke_h/2 - 1])  cube([yoke_w, 1, 1]);
      }

      // azimuth pilot boss -- takes the shear, forces the joint to rotate
      // about one axis instead of wandering while you tighten it
      translate([0, az_center_y, yoke_h/2 + yoke_pad_t])
        cylinder(d = az_pilot_d, h = az_pilot_h);
    }

    // elevation bolts
    ifc_holes(yoke_t + 2);

    // azimuth bolts -- plain holes here, arc slots in the leg
    translate([0, az_center_y, yoke_h/2 - 1])
      for (i = [0 : az_bolt_n - 1])
        rotate([0, 0, i*360/az_bolt_n])
          translate([az_bolt_r, 0, 0])
            cylinder(d = az_bolt_d, h = yoke_pad_t + 2);

    // ALIGNMENT REFERENCE 4: engraved azimuth protractor, 2 deg ticks, on the
    // rim of the pad outboard of az_pad_r where the leg does not cover it.
    // The leg carries the pointer.  Read the coarse azimuth off this before
    // trimming over CAN, and WRITE IT DOWN -- CAN_RX_ANGLE_MOUNTING_OFFSET has
    // only 8 degrees to give and you want to know how much you already spent.
    translate([0, az_center_y, yoke_h/2 + yoke_pad_t - 0.8])
      for (k = [-az_range_deg : 2 : az_range_deg])
        rotate([0, 0, k])
          translate([az_pad_r - 2, -0.6, 0])
            cube([az_dial_r - az_pad_r + 2, 1.2, 1.2]);

    // deeper, wider tick at zero
    translate([0, az_center_y, yoke_h/2 + yoke_pad_t - 1.8])
      translate([az_pad_r - 6, -1.2, 0])
        cube([az_dial_r - az_pad_r + 6, 2.4, 2.4]);
  }
}


// =============================================================================
// LEG, CONCEPT A -- yoke pad up and forward to the rear bumper reinforcement
// beam.
//
// EVERYTHING ABOUT THE CAR-SIDE END OF THIS PART IS A GUESS.  The rear of this
// car has no factory radar mounting points.  The beam is steel, bolted to the
// frame rails through the crash cans, and whether it carries usable holes,
// studs, or a flange you can clamp is UNMEASURED.  The slots are deliberately
// long because they will be in the wrong place.
//
// Do not drill the beam until you have looked for an existing fastener.  If
// nothing exists, a band clamp around the beam beats a new hole in a crash
// structure.
//
// PRINT ORIENTATION: profile in the bed plane, extruded along X, same reason
// as the yoke.
// =============================================================================
module leg_beam() {
  y_pad = az_center_y;              // center of the azimuth joint
  y_car = y_pad - leg_reach;        // forward face, where the beam is

  difference() {
    union() {
      // horizontal pad that mates the yoke
      translate([0, y_pad, 0]) cylinder(r = az_pad_r, h = leg_t);

      // arm forward
      translate([-leg_pad_w/2, y_car, 0]) cube([leg_pad_w, leg_reach, leg_t]);

      // car-side vertical pad, rising to the beam
      translate([-leg_pad_w/2, y_car, 0])
        cube([leg_pad_w, leg_t, leg_rise + leg_pad_h]);

      // gusset across the inside corner
      hull() {
        translate([-leg_pad_w/2, y_car + leg_t, leg_t + 45])   cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_car + leg_t + 45, leg_t])   cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_car + leg_t, leg_t])        cube([leg_pad_w, 1, 1]);
      }

      // ALIGNMENT REFERENCE 5: the azimuth pointer, overhanging the yoke's
      // dial.  Rooted inside the pad so it is not a floating tab.
      translate([0, y_pad, 0])
        linear_extrude(leg_t)
          polygon([[az_pad_r - 14, -5], [az_pad_r - 14, 5], [az_dial_r - 4, 0]]);
    }

    // azimuth ARC SLOTS -- this is the coarse azimuth adjustment.  Loosen,
    // swing, read the dial, retighten.  Anything left over goes to
    // CAN_RX_ANGLE_MOUNTING_OFFSET, which has +/-8 deg and no more.
    translate([0, y_pad, -1])
      for (i = [0 : az_bolt_n - 1])
        rotate([0, 0, i*360/az_bolt_n])
          arc_slot(az_bolt_r, az_range_deg, az_bolt_d, leg_t + 4);

    // pilot bore over the yoke's boss
    translate([0, y_pad, -1]) cylinder(d = az_pilot_d + 0.4, h = leg_t + 2);

    // car-side slots.  MEASURE the beam and replace these outright.
    for (sx = [-1, 1])
      translate([sx*leg_slot_dx - leg_slot_len/2, y_car - 1,
                 leg_rise + leg_pad_h/2])
        hull() {
          rotate([-90, 0, 0]) cylinder(d = leg_slot_d, h = leg_t + 2);
          translate([leg_slot_len, 0, 0])
            rotate([-90, 0, 0]) cylinder(d = leg_slot_d, h = leg_t + 2);
        }
  }
}


// =============================================================================
// LEG, CONCEPT B -- external drop below the valance.
//
// Same yoke interface.  Longer, and it sees weather, sun and stone chips, so
// this one is ASA or PC and gets the hood.
// =============================================================================
module leg_drop() {
  y_pad = az_center_y;
  y_col = y_pad - az_pad_r;     // the column runs up the forward side

  difference() {
    union() {
      translate([0, y_pad, 0]) cylinder(r = az_pad_r, h = leg_t);

      // vertical column
      translate([-leg_pad_w/2, y_col, 0])
        cube([leg_pad_w, leg_t, drop_length]);

      // top pad, onto whatever the car offers up there
      translate([-leg_pad_w/2, y_col - leg_reach, drop_length - leg_t])
        cube([leg_pad_w, leg_reach + leg_t, leg_t]);

      // gusset at the top corner
      hull() {
        translate([-leg_pad_w/2, y_col, drop_length - leg_t - 50]) cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_col - 50, drop_length - leg_t])  cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_col, drop_length - leg_t])       cube([leg_pad_w, 1, 1]);
      }

      // gusset at the bottom corner, into the azimuth pad
      hull() {
        translate([-leg_pad_w/2, y_col, 40])                cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_col + 40, leg_t - 1])    cube([leg_pad_w, 1, 1]);
        translate([-leg_pad_w/2, y_col, leg_t - 1])         cube([leg_pad_w, 1, 1]);
      }

      translate([0, y_pad, 0])
        linear_extrude(leg_t)
          polygon([[az_pad_r - 14, -5], [az_pad_r - 14, 5], [az_dial_r - 4, 0]]);
    }

    translate([0, y_pad, -1])
      for (i = [0 : az_bolt_n - 1])
        rotate([0, 0, i*360/az_bolt_n])
          arc_slot(az_bolt_r, az_range_deg, az_bolt_d, leg_t + 4);

    translate([0, y_pad, -1]) cylinder(d = az_pilot_d + 0.4, h = leg_t + 2);

    // car-side holes in the top pad.  MEASURE and replace.
    for (sx = [-1, 1])
      translate([sx*leg_slot_dx, y_col - leg_reach/2, drop_length - leg_t - 1])
        cylinder(d = leg_slot_d, h = leg_t + 2);
  }
}


// =============================================================================
// HOOD -- Concept B only.  Sheds spray and stone chips off the antenna face
// without putting anything in the boresight.
//
// It must not extend into the beam.  It sits ABOVE the face and projects
// rearward; the radar looks out under it.
// =============================================================================
module hood() {
  difference() {
    union() {
      translate([-cradle_w/2, back_t, cav_h/2 + strap_t])
        cube([cradle_w, cav_d + hood_overhang, 4]);
      for (sx = [-1, 1])
        translate([sx*(cradle_w/2 - 4), back_t, cav_h/2 + strap_t - 25])
          cube([4, cav_d + hood_overhang, 25]);
    }
    // drip groove, so water runs off the lip instead of tracking back along
    // the underside onto the antenna face
    translate([-cradle_w/2 - 1, back_t + cav_d + hood_overhang - 6,
               cav_h/2 + strap_t - 1])
      cube([cradle_w + 2, 2, 2]);
  }
}


// =============================================================================
// ASSEMBLY
// =============================================================================
module assembly() {
  yoke();

  // cradle, tilted by the shim angle and stood off by the shim thickness
  translate([0, shim_base_t, 0])
    rotate([-shim_angle_deg, 0, 0]) {
      color("steelblue") cradle();
      translate([0, 0, cav_h/2]) color("lightsteelblue") strap();
      translate([0, back_t, 0]) radar_block();
    }

  color("orange") shim(shim_angle_deg);

  translate([0, 0, yoke_h/2 + yoke_pad_t])
    color("olivedrab") leg_beam();
}


// =============================================================================
// DISPATCH
// =============================================================================
if      (part == "assembly") assembly();
else if (part == "cradle")   cradle();
else if (part == "strap")    strap();
else if (part == "shim")     rotate([90, 0, 0]) shim(shim_angle_deg);   // laid flat to print
else if (part == "shim_set") shim_set();
else if (part == "yoke")     yoke();
else if (part == "leg_beam") leg_beam();
else if (part == "leg_drop") leg_drop();
else if (part == "hood")     hood();
else                         echo("unknown part");
