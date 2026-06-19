using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

# you can rename the struct, but don't change the identifier
struct CarrotMan @0x81c2f05a394cf4af {
	activeCarrot @0 : Int32;
	nRoadLimitSpeed @1 : Int32;
	remote @2 : Text;
	xSpdType @3 : Int32;
	xSpdLimit @4 : Int32;
	xSpdDist @5 : Int32;
	xSpdCountDown @6 : Int32;
	xTurnInfo @7 : Int32;
	xDistToTurn @8 : Int32;
	xTurnCountDown @9 : Int32;
	atcType @10 : Text;
	vTurnSpeed @11 : Int32;
	szPosRoadName @12 : Text;
	szTBTMainText @13 : Text;
	desiredSpeed @14 : Int32;
	desiredSource @15 : Text;
	carrotCmdIndex @16 : Int32;
	carrotCmd @17 : Text;
	carrotArg @18 : Text;
	xPosLat @19 : Float32;
	xPosLon @20 : Float32;
	xPosAngle @21 : Float32;
	xPosSpeed @22 : Float32;
	trafficState @23 : Int32;
	nGoPosDist @24 : Int32;
	nGoPosTime @25 : Int32;
	szSdiDescr @26 : Text;
	naviPaths @27 : Text;
	leftSec @28 : Int32;
}

struct YoloDetection @0xf35cc4560bbf6ec2 {
  classId @0 :UInt16;
  className @1 :Text;
  confidence @2 :Float32;
  x @3 :Float32;
  y @4 :Float32;
  w @5 :Float32;
  h @6 :Float32;
  distanceEstimate @7 :Float32;
}

struct YoloObjectData @0xaedffd8f31e7b55d {
  hasRedLight @0 :Bool;
  hasGreenLight @1 :Bool;
  distanceEstimate @2 :Float32;
  yoloClass @3 :Text;
  x @4 :Float32;
  y @5 :Float32;
  w @6 :Float32;
  h @7 :Float32;
  detections @8 :List(YoloDetection);
  frameId @9 :UInt64;
  inferenceTimeMs @10 :UInt32;
  numDetections @11 :UInt16;
  roundTripMs @12 :UInt32;   # full pipeline: frame sent → result received on comma3
}

struct LaneMarkingState @0xda96579883444c35 {
  frameId @0 :UInt64;
  timestampEof @1 :UInt64;
  modelExecutionTimeMs @2 :Float32;
  valid @3 :Bool;
  leftLabel @4 :Text;
  rightLabel @5 :Text;
  leftConfidence @6 :Float32;
  rightConfidence @7 :Float32;
  leftDecision @8 :Text;
  rightDecision @9 :Text;
  leftBoundaryY @10 :Float32;
  rightBoundaryY @11 :Float32;
  laneWidth @12 :Float32;
  leftBlock @13 :Bool;
  rightBlock @14 :Bool;
  leftNoBlock @15 :Bool;
  rightNoBlock @16 :Bool;
  cutInAssist @17 :Bool;
  inferenceSkipped @18 :Bool;
}

struct SideVisionState @0x80ae746ee2596b11 {
  frameId @0 :UInt64;
  timestampEof @1 :UInt64;
  roadFrameId @2 :UInt64;
  valid @3 :Bool;
  modelExecutionTimeMs @4 :Float32;
  inferenceSkipped @5 :Bool;

  leftCameraProb @6 :Float32;
  rightCameraProb @7 :Float32;
  leftCameraDetected @8 :Bool;
  rightCameraDetected @9 :Bool;

  leftBsd @10 :Bool;
  rightBsd @11 :Bool;
  leftRadarDetected @12 :Bool;
  rightRadarDetected @13 :Bool;
  leftRadarDRel @14 :Float32;
  rightRadarDRel @15 :Float32;
  leftCornerLongDist @16 :Float32;
  rightCornerLongDist @17 :Float32;
  leftCornerLatDist @18 :Float32;
  rightCornerLatDist @19 :Float32;

  leftBlocked @20 :Bool;
  rightBlocked @21 :Bool;
  leftReason @22 :Text;
  rightReason @23 :Text;
  source @24 :Text;
  leftRoadCameraDetected @25 :Bool;
  rightRoadCameraDetected @26 :Bool;
  leftRoadCameraDRel @27 :Float32;
  rightRoadCameraDRel @28 :Float32;
  leftRoadCameraProb @29 :Float32;
  rightRoadCameraProb @30 :Float32;
}

struct TrafficLightStopLineState @0xa5cd762cd951a455 {
  frameId @0 :UInt64;
  timestampEof @1 :UInt64;
  valid @2 :Bool;
  inferenceSkipped @3 :Bool;
  modelLoaded @4 :Bool;
  modelRuntime @5 :Text;
  modelExecutionTimeMs @6 :Float32;
  processingWidth @7 :UInt16;

  trafficState @8 :Text;
  trafficConfidence @9 :Float32;
  trafficRedProb @10 :Float32;
  trafficGreenProb @11 :Float32;
  trafficYellowProb @12 :Float32;
  trafficLeftProb @13 :Float32;
  trafficNoSignalProb @14 :Float32;
  trafficCandidateCount @15 :UInt16;
  trafficCandidateX @16 :Float32;
  trafficCandidateY @17 :Float32;
  trafficCandidateW @18 :Float32;
  trafficCandidateH @19 :Float32;

  stopLineState @20 :Text;
  stopLineConfidence @21 :Float32;
  stopLineScore @22 :Float32;
  crosswalkScore @23 :Float32;
  stopLineYNorm @24 :Float32;
  stopLineWidthNorm @25 :Float32;
  stopLineStripeCount @26 :UInt16;
  leadBlocked @27 :Bool;
  darkOrGlare @28 :Bool;
  source @29 :Text;
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
}

struct CustomReserved7 @0xb86e6369214c01c8 {
}

struct CustomReserved8 @0xf416ec09499d9d19 {
}

struct CustomReserved9 @0xa1680744031fdb2d {
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}

struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct CustomReserved13 @0xcd96dafb67a082d0 {
}

struct CustomReserved14 @0xb057204d7deadf3f {
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

struct CustomReserved17 @0xa30662f84033036c {
}

struct CustomReserved18 @0xc86a3d38d13eb3ef {
}

struct CustomReserved19 @0xa4f1eb3323f5f582 {
}
