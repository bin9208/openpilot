package com.naver.map.core.navigation.model;

import com.naver.maps.navi.v2.shared.api.route.model.RouteInfo;

public final class NaviRouteData {
  private final RouteInfo info;

  public NaviRouteData(RouteInfo info) {
    this.info = info;
  }

  public RouteInfo h() {
    return info;
  }
}
