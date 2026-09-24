package com.naver.map.core.common.model;

public abstract class SafetyExtra {
  private SafetyExtra() {
  }

  public static final class Distance extends SafetyExtra {
    private final double distanceM;
    private final boolean throwing;

    public Distance(double distanceM) {
      this(distanceM, false);
    }

    public Distance(double distanceM, boolean throwing) {
      this.distanceM = distanceM;
      this.throwing = throwing;
    }

    public double getDistance() {
      if (throwing) {
        throw new IllegalStateException("distance unavailable");
      }
      return distanceM;
    }
  }

  public static final class SchoolZone extends SafetyExtra {
    private final double distanceM;

    public SchoolZone(double distanceM) {
      this.distanceM = distanceM;
    }

    public double getDistance() {
      return distanceM;
    }
  }
}
