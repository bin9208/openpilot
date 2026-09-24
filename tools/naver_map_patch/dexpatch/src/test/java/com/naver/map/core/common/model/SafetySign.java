package com.naver.map.core.common.model;

import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;

public final class SafetySign {
  private final SignType type;
  private final Integer speedKph;
  private final boolean throwingType;
  private final boolean throwingSpeed;

  public SafetySign(SignType type, Integer speedKph) {
    this(type, speedKph, false, false);
  }

  public SafetySign(
      SignType type, Integer speedKph, boolean throwingType, boolean throwingSpeed) {
    this.type = type;
    this.speedKph = speedKph;
    this.throwingType = throwingType;
    this.throwingSpeed = throwingSpeed;
  }

  public SignType getType() {
    if (throwingType) {
      throw new IllegalStateException("type unavailable");
    }
    return type;
  }

  public Integer getSpeed() {
    if (throwingSpeed) {
      throw new IllegalStateException("speed unavailable");
    }
    return speedKph;
  }

  public abstract static class SignType {
    private SignType() {
    }

    public static final class Safety extends SignType {
      private final SafetyCode code;
      private final boolean throwingCode;

      public Safety(SafetyCode code) {
        this(code, false);
      }

      public Safety(SafetyCode code, boolean throwingCode) {
        this.code = code;
        this.throwingCode = throwingCode;
      }

      public SafetyCode getCode() {
        if (throwingCode) {
          throw new IllegalStateException("code unavailable");
        }
        return code;
      }
    }

    public static final class Other extends SignType {
    }
  }
}
