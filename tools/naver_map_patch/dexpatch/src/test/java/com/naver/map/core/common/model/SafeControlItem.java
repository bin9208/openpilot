package com.naver.map.core.common.model;

public final class SafeControlItem {
  private final SafetySign sign;
  private final SafetyExtra extra;
  private final SafetyExtra sectionExtra;
  private final boolean throwingSign;
  private final boolean throwingExtra;
  private final boolean throwingSectionExtra;

  public SafeControlItem(SafetySign sign, SafetyExtra extra, SafetyExtra sectionExtra) {
    this(sign, extra, sectionExtra, false, false, false);
  }

  public SafeControlItem(
      SafetySign sign,
      SafetyExtra extra,
      SafetyExtra sectionExtra,
      boolean throwingSign,
      boolean throwingExtra,
      boolean throwingSectionExtra) {
    this.sign = sign;
    this.extra = extra;
    this.sectionExtra = sectionExtra;
    this.throwingSign = throwingSign;
    this.throwingExtra = throwingExtra;
    this.throwingSectionExtra = throwingSectionExtra;
  }

  public SafetySign getSign() {
    if (throwingSign) {
      throw new IllegalStateException("sign unavailable");
    }
    return sign;
  }

  public SafetyExtra getExtra() {
    if (throwingExtra) {
      throw new IllegalStateException("extra unavailable");
    }
    return extra;
  }

  public SafetyExtra getSectionExtra() {
    if (throwingSectionExtra) {
      throw new IllegalStateException("section extra unavailable");
    }
    return sectionExtra;
  }
}
