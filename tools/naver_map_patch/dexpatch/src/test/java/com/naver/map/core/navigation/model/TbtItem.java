package com.naver.map.core.navigation.model;

public final class TbtItem {
  public enum Type {
    NORMAL
  }

  private final double distanceM;
  private final TbtDataItem data;
  private final boolean throwingDistance;
  private final boolean throwingData;
  private final boolean throwingType;

  public TbtItem(double distanceM, TbtDataItem data) {
    this(distanceM, data, false, false, false);
  }

  public TbtItem(
      double distanceM,
      TbtDataItem data,
      boolean throwingDistance,
      boolean throwingData,
      boolean throwingType) {
    this.distanceM = distanceM;
    this.data = data;
    this.throwingDistance = throwingDistance;
    this.throwingData = throwingData;
    this.throwingType = throwingType;
  }

  public double h() {
    if (throwingDistance) {
      throw new IllegalStateException("distance unavailable");
    }
    return distanceM;
  }

  public TbtDataItem i() {
    if (throwingData) {
      throw new IllegalStateException("data unavailable");
    }
    return data;
  }

  public Type j() {
    if (throwingType) {
      throw new IllegalStateException("type unavailable");
    }
    return Type.NORMAL;
  }
}
