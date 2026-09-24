package com.naver.maps.navi.v2.shared.api.route.constants;

public enum TurnPointType {
  Left,
  Right,
  Direction11,
  RightDirection,
  UTurn,
  Goal,
  AccessUnderpassStraight,
  Rest;

  public int getValue() {
    throw new AssertionError("production mapping must not call TurnPointType.getValue()");
  }
}
