package com.naver.map.core.navigation.model;

public final class CurrentRoute {
  private final NaviRouteData data;

  public CurrentRoute(NaviRouteData data) {
    this.data = data;
  }

  public NaviRouteData e() {
    return data;
  }
}
