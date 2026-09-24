package com.naver.maps.navi.v2.shared.api.route.model;

import java.util.List;

public final class RouteInfoImpl implements RouteInfo {
  private final List<?> pathPoints;

  public RouteInfoImpl(List<?> pathPoints) {
    this.pathPoints = pathPoints;
  }

  @Override
  public List<?> getPathPoints() {
    return pathPoints;
  }
}
