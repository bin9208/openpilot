package com.naver.map.core.navigation;

public final class NaviStatusBroadcaster {
  private NaviStatusBroadcaster() {
  }

  public abstract static class Status {
    private Status() {
    }

    public static final class Guiding extends Status {
      public Guiding() {
      }
    }

    public static final class Stopped extends Status {
      public Stopped() {
      }
    }

    public static final class Arrived extends Status {
      public Arrived() {
      }
    }
  }
}
