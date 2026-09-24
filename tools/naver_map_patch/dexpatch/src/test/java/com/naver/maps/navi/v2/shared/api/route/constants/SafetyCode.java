package com.naver.maps.navi.v2.shared.api.route.constants;

public enum SafetyCode {
  SpeedCam(true, false),
  MoveSpeedCam(true, false),
  StartSectionSpeedCam(true, false),
  EndSectionSpeedCam(true, false),
  VariableSectionStart(true, false),
  VariableSectionEnd(true, false),
  SpeedBump(false, true),
  BusOnly(false, false);

  private final boolean allSpeedCameras;
  private final boolean speedBump;
  private static boolean throwingAllSpeedCameras;
  private static boolean throwingSpeedBump;

  SafetyCode(boolean allSpeedCameras, boolean speedBump) {
    this.allSpeedCameras = allSpeedCameras;
    this.speedBump = speedBump;
  }

  public boolean isAllSpeedCameras() {
    if (throwingAllSpeedCameras) {
      throw new IllegalStateException("camera classifier unavailable");
    }
    return allSpeedCameras;
  }

  public boolean isSpeedBump() {
    if (throwingSpeedBump) {
      throw new IllegalStateException("bump classifier unavailable");
    }
    return speedBump;
  }

  public static void setThrowingAllSpeedCamerasForTesting(boolean throwing) {
    throwingAllSpeedCameras = throwing;
  }

  public static void setThrowingSpeedBumpForTesting(boolean throwing) {
    throwingSpeedBump = throwing;
  }
}
