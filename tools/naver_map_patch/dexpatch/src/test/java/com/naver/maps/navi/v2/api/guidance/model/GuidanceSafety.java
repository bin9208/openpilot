package com.naver.maps.navi.v2.api.guidance.model;

import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;

public interface GuidanceSafety {
  SafetyCode getCode();

  double distance();
}
