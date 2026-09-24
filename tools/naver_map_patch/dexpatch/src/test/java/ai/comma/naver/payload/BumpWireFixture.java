package ai.comma.naver.payload;

import com.naver.map.core.common.model.SafeControlItem;
import com.naver.map.core.common.model.SafetySign;
import com.naver.map.core.navigation.NaviStatusBroadcaster;
import com.naver.maps.navi.v2.api.guidance.model.GuidanceSafety;
import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;

/** Synthetic exact-class input; exercises real reflection, runtime and envelope. */
public final class BumpWireFixture {
  public static void main(String[] args) {
    NaverNavigationSender sender = new NaverNavigationSender(
        () -> new java.io.ByteArrayOutputStream(), () -> 0L) {
      @Override public synchronized void offer(NaverNavigationState state) { }
    };
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds("00000000-0000-4000-8000-000000000001")), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(new GuidanceSafety() {
      @Override public SafetyCode getCode() { return SafetyCode.SpeedBump; }
      @Override public double distance() { return 30.0; }
    });
    runtime.onSafety(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null), null, null));
    System.out.println(runtime.snapshotJson());
    runtime.onStatus(new NaviStatusBroadcaster.Status.Stopped());
    System.out.println(runtime.snapshotJson());
  }
}
