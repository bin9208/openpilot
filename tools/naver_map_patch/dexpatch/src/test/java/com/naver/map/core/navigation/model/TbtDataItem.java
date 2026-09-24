package com.naver.map.core.navigation.model;

import com.naver.maps.navi.v2.shared.api.route.constants.TurnPointType;

public final class TbtDataItem {
  private final TurnPointType maneuver;
  private final String mainText;
  private final String roadName;
  private final boolean throwingManeuver;
  private final boolean throwingMainText;
  private final boolean throwingRoadName;

  public TbtDataItem(TurnPointType maneuver, String mainText, String roadName) {
    this(maneuver, mainText, roadName, false, false, false);
  }

  public TbtDataItem(
      TurnPointType maneuver,
      String mainText,
      String roadName,
      boolean throwingManeuver,
      boolean throwingMainText,
      boolean throwingRoadName) {
    this.maneuver = maneuver;
    this.mainText = mainText;
    this.roadName = roadName;
    this.throwingManeuver = throwingManeuver;
    this.throwingMainText = throwingMainText;
    this.throwingRoadName = throwingRoadName;
  }

  public TurnPointType s() {
    if (throwingManeuver) {
      throw new IllegalStateException("maneuver unavailable");
    }
    return maneuver;
  }

  public String n() {
    if (throwingMainText) {
      throw new IllegalStateException("main text unavailable");
    }
    return mainText;
  }

  public String v() {
    if (throwingRoadName) {
      throw new IllegalStateException("road name unavailable");
    }
    return roadName;
  }
}
