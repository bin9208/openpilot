package ai.comma.naver.payload;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

public final class Naver6805ObjectMapper {
  private static final String STATUS_GUIDING =
      "com.naver.map.core.navigation.NaviStatusBroadcaster$Status$Guiding";
  private static final String STATUS_STOPPED =
      "com.naver.map.core.navigation.NaviStatusBroadcaster$Status$Stopped";
  private static final String STATUS_ARRIVED =
      "com.naver.map.core.navigation.NaviStatusBroadcaster$Status$Arrived";
  private static final String TBT_ITEM =
      "com.naver.map.core.navigation.model.TbtItem";
  private static final String TBT_DATA =
      "com.naver.map.core.navigation.model.TbtDataItem";
  private static final String TURN_POINT_TYPE =
      "com.naver.maps.navi.v2.shared.api.route.constants.TurnPointType";
  private static final String SAFE_ITEM =
      "com.naver.map.core.common.model.SafeControlItem";
  private static final String SAFETY_SIGN =
      "com.naver.map.core.common.model.SafetySign";
  private static final String SAFETY_TYPE =
      "com.naver.map.core.common.model.SafetySign$SignType";
  private static final String SAFETY =
      "com.naver.map.core.common.model.SafetySign$SignType$Safety";
  private static final String SAFETY_EXTRA =
      "com.naver.map.core.common.model.SafetyExtra";
  private static final String DISTANCE =
      "com.naver.map.core.common.model.SafetyExtra$Distance";
  private static final String SAFETY_CODE =
      "com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode";
  private static final String GUIDANCE_SAFETY =
      "com.naver.maps.navi.v2.api.guidance.model.GuidanceSafety";
  private static final String CURRENT_ROUTE =
      "com.naver.map.core.navigation.model.CurrentRoute";
  private static final String NAVI_ROUTE_DATA =
      "com.naver.map.core.navigation.model.NaviRouteData";
  private static final String ROUTE_INFO =
      "com.naver.maps.navi.v2.shared.api.route.model.RouteInfo";
  private static final String LAT_LNG = "com.naver.maps.geometry.LatLng";
  private static final int MAX_ROUTE_POINTS = 4096;
  private static final int MAX_TEXT = 256;
  private static final int MAX_DIAGNOSTIC_CODES = 32;
  private static final Diagnostics DIAGNOSTICS = new Diagnostics(MAX_DIAGNOSTIC_CODES);
  private static final Object METHOD_CACHE_LOCK = new Object();
  private static final Map<MethodKey, Method> METHOD_CACHE =
      new HashMap<MethodKey, Method>();

  private Naver6805ObjectMapper() {
  }

  public static Optional<MappedUpdate> map(String channel, Object value) {
    if ("status".equals(channel)) {
      String lifecycle = mapLifecycle(value);
      if ("unknown".equals(lifecycle)) {
        DIAGNOSTICS.record("status_lifecycle");
        return Optional.empty();
      }
      return Optional.of(MappedUpdate.status(lifecycle));
    }
    if ("tbt_current".equals(channel)) {
      Guidance guidance = mapGuidance(value);
      return Optional.of(MappedUpdate.current(guidance));
    }
    if ("tbt_next".equals(channel)) {
      Guidance guidance = mapGuidance(value);
      return Optional.of(MappedUpdate.next(guidance));
    }
    if ("safety".equals(channel)) {
      Safety safety = mapSafety(value);
      return Optional.of(MappedUpdate.safety(safety));
    }
    if ("route".equals(channel)) {
      return Optional.of(MappedUpdate.route(mapRoute(value)));
    }
    DIAGNOSTICS.record("unknown_channel");
    return Optional.empty();
  }

  public static String mapLifecycle(Object status) {
    String name = className(status);
    if (STATUS_GUIDING.equals(name)) {
      return "guiding";
    }
    if (STATUS_STOPPED.equals(name)) {
      return "stopped";
    }
    if (STATUS_ARRIVED.equals(name)) {
      return "arrived";
    }
    return "unknown";
  }

  public static Guidance mapGuidance(Object item) {
    try {
      if (!isExact(item, TBT_ITEM)) {
        DIAGNOSTICS.record("guidance_root_descriptor");
        return Guidance.absent();
      }
      double distanceM = number(invoke(item, TBT_ITEM, "h", double.class));
      if (!validDistance(distanceM, true)) {
        DIAGNOSTICS.record("guidance_distance");
        return Guidance.absent();
      }
      Object data = invoke(item, TBT_ITEM, "i", classForName(TBT_DATA));
      if (!isExact(data, TBT_DATA)) {
        DIAGNOSTICS.record("guidance_data_descriptor");
        return Guidance.absent();
      }
      Object maneuverObject = invoke(data, TBT_DATA, "s", classForName(TURN_POINT_TYPE));
      String maneuver = mapManeuver(maneuverObject);
      if (maneuver == null) {
        DIAGNOSTICS.record("guidance_maneuver");
        return Guidance.absent();
      }
      String mainText = trimText(invokeNullable(data, TBT_DATA, "n", String.class));
      String roadName = trimText(invokeNullable(data, TBT_DATA, "v", String.class));
      return new Guidance(true, maneuver, distanceM, roadName, mainText);
    } catch (ReflectiveOperationException | RuntimeException error) {
      DIAGNOSTICS.record("guidance_accessor");
      return Guidance.absent();
    }
  }

  public static Route mapRoute(Object value) {
    if (!isExact(value, CURRENT_ROUTE)) {
      return invalidRoute("route_root_descriptor", 0);
    }
    try {
      Object data = invoke(value, CURRENT_ROUTE, "e", classForName(NAVI_ROUTE_DATA));
      if (!isExact(data, NAVI_ROUTE_DATA)) {
        return invalidRoute("route_accessor", 0);
      }
      Object info = invoke(data, NAVI_ROUTE_DATA, "h", classForName(ROUTE_INFO));
      Object rawPoints = invokeInterface(
          info, ROUTE_INFO, "getPathPoints", List.class);
      if (!(rawPoints instanceof List<?>)) {
        return invalidRoute("route_accessor", 0);
      }
      List<?> input = (List<?>) rawPoints;
      int inputCount = input.size();
      if (inputCount < 2) {
        return invalidRoute("route_accessor", inputCount);
      }

      Class<?> pointClass = classForName(LAT_LNG);
      Field latitude = coordinateField(pointClass, "latitude");
      Field longitude = coordinateField(pointClass, "longitude");
      int outputCount = Math.min(inputCount, MAX_ROUTE_POINTS);
      int inputLastIndex = inputCount - 1;
      int outputLastIndex = outputCount - 1;
      List<RoutePoint> points = new ArrayList<RoutePoint>(outputCount);
      for (int outputIndex = 0; outputIndex < outputCount; outputIndex++) {
        Object point = input.get(sampleIndex(outputIndex, inputLastIndex, outputLastIndex));
        if (!isExact(point, LAT_LNG)) {
          return invalidRoute("route_point_descriptor", inputCount);
        }
        double latitudeValue = latitude.getDouble(point);
        double longitudeValue = longitude.getDouble(point);
        if (!validCoordinate(latitudeValue, -90.0, 90.0)
            || !validCoordinate(longitudeValue, -180.0, 180.0)) {
          return invalidRoute("route_coordinate", inputCount);
        }
        points.add(new RoutePoint(latitudeValue, longitudeValue));
      }
      return new Route(true, points, inputCount, outputCount, "route_ok");
    } catch (ReflectiveOperationException | RuntimeException error) {
      return invalidRoute("route_accessor", 0);
    }
  }

  static int sampleIndex(int outputIndex, int inputLastIndex, int outputLastIndex) {
    return (int) Math.rint(outputIndex * inputLastIndex / (double) outputLastIndex);
  }

  public static Safety mapSafety(Object item) {
    return mapSafety(item, SafetySource.absent(), true);
  }

  public static SafetySource mapSafetySource(Object value) {
    try {
      if (value == null || !classForName(GUIDANCE_SAFETY).isInstance(value)) {
        return invalidSafetySource("safety_source_descriptor");
      }
      Object code = invokeInterface(
          value, GUIDANCE_SAFETY, "getCode", classForName(SAFETY_CODE));
      if (!isExact(code, SAFETY_CODE)) {
        return invalidSafetySource("safety_source_code");
      }
      String codeName = enumName(code);
      boolean speedBump = bool(invoke(code, SAFETY_CODE, "isSpeedBump", boolean.class));
      if (!speedBump) {
        return invalidSafetySource("safety_source_code");
      }
      double distanceM = number(invokeInterface(
          value, GUIDANCE_SAFETY, "distance", double.class));
      if (!validDistance(distanceM, false)) {
        return invalidSafetySource("safety_source_distance");
      }
      return new SafetySource(
          true, codeName, true, distanceM, "safety_source_ok");
    } catch (ReflectiveOperationException | RuntimeException error) {
      return invalidSafetySource("safety_source_accessor");
    }
  }

  public static Safety mapSafety(Object item, SafetySource source) {
    return mapSafety(item, source, false);
  }

  private static Safety mapSafety(
      Object item, SafetySource source, boolean allowDisplayExtraForSpeedBump) {
    try {
      if (!isExact(item, SAFE_ITEM)) {
        DIAGNOSTICS.record("safety_root_descriptor");
        return Safety.absent();
      }
      Object sign = invokeNullable(item, SAFE_ITEM, "getSign", classForName(SAFETY_SIGN));
      if (!isExact(sign, SAFETY_SIGN)) {
        DIAGNOSTICS.record("safety_sign_descriptor");
        return Safety.absent();
      }
      Object type = invokeNullable(sign, SAFETY_SIGN, "getType", classForName(SAFETY_TYPE));
      if (!isExact(type, SAFETY)) {
        DIAGNOSTICS.record("safety_type_descriptor");
        return Safety.absent();
      }
      Object code = invokeNullable(type, SAFETY, "getCode", classForName(SAFETY_CODE));
      if (!isExact(code, SAFETY_CODE)) {
        DIAGNOSTICS.record("safety_code_descriptor");
        return Safety.absent();
      }
      String codeName = enumName(code);
      boolean speedBump = bool(invoke(code, SAFETY_CODE, "isSpeedBump", boolean.class));
      if (speedBump) {
        double distanceM = allowDisplayExtraForSpeedBump
            ? distanceFrom(invokeNullable(
                item, SAFE_ITEM, "getExtra", classForName(SAFETY_EXTRA)))
            : speedBumpDistanceFrom(source, code);
        return new Safety(true, "speed_bump", distanceM, 0);
      }
      String kind = cameraKind(codeName);
      if (kind == null && bool(invoke(
          code, SAFETY_CODE, "isAllSpeedCameras", boolean.class))) {
        kind = "fixed_camera";
      }
      if (kind == null) {
        DIAGNOSTICS.record("safety_kind");
        return Safety.absent();
      }
      double distanceM = distanceFrom(invokeNullable(
          item,
          SAFE_ITEM,
          "section_camera".equals(kind) ? "getSectionExtra" : "getExtra",
          classForName(SAFETY_EXTRA)));
      Integer speedKph = integer(invokeNullable(sign, SAFETY_SIGN, "getSpeed", Integer.class));
      if (speedKph == null || speedKph.intValue() <= 0 || speedKph.intValue() > 250) {
        DIAGNOSTICS.record("safety_speed");
        return Safety.absent();
      }
      return new Safety(true, kind, distanceM, speedKph.intValue());
    } catch (ReflectiveOperationException | RuntimeException error) {
      DIAGNOSTICS.record("safety_distance_accessor");
      return Safety.absent();
    }
  }

  public static Diagnostics diagnosticsForTesting() {
    return DIAGNOSTICS;
  }

  public static void resetDiagnosticsForTesting() {
    DIAGNOSTICS.clear();
  }

  static void clearMethodCacheForTesting() {
    synchronized (METHOD_CACHE_LOCK) {
      METHOD_CACHE.clear();
    }
  }

  static int methodCacheSizeForTesting() {
    synchronized (METHOD_CACHE_LOCK) {
      return METHOD_CACHE.size();
    }
  }

  static Optional<Object> invokeForTesting(
      Object target, String ownerName, String methodName, Class<?> returnType) {
    try {
      return Optional.ofNullable(invokeNullable(target, ownerName, methodName, returnType));
    } catch (ReflectiveOperationException | RuntimeException error) {
      return Optional.empty();
    }
  }

  private static Object invoke(
      Object target, String ownerName, String methodName, Class<?> returnType)
      throws ReflectiveOperationException {
    Object value = invokeNullable(target, ownerName, methodName, returnType);
    if (value == null && returnType != Void.TYPE && !returnType.isPrimitive()) {
      throw new NoSuchMethodException(ownerName + "#" + methodName + " returned null");
    }
    return value;
  }

  private static Object invokeNullable(
      Object target, String ownerName, String methodName, Class<?> returnType)
      throws ReflectiveOperationException {
    if (target == null || !ownerName.equals(target.getClass().getName())) {
      throw new NoSuchMethodException(ownerName + "#" + methodName);
    }
    Method method = lookupMethod(target.getClass(), ownerName, methodName, returnType);
    try {
      return method.invoke(target);
    } catch (InvocationTargetException error) {
      Throwable cause = error.getCause();
      if (cause instanceof RuntimeException) {
        throw (RuntimeException) cause;
      }
      throw error;
    }
  }

  private static Object invokeInterface(
      Object target, String ownerName, String methodName, Class<?> returnType)
      throws ReflectiveOperationException {
    Class<?> ownerClass = classForName(ownerName);
    if (target == null || !ownerClass.isInterface() || !ownerClass.isInstance(target)) {
      throw new NoSuchMethodException(ownerName + "#" + methodName);
    }
    Method method = lookupMethod(ownerClass, ownerName, methodName, returnType);
    try {
      Object value = method.invoke(target);
      if (value == null && returnType != Void.TYPE && !returnType.isPrimitive()) {
        throw new NoSuchMethodException(ownerName + "#" + methodName + " returned null");
      }
      return value;
    } catch (InvocationTargetException error) {
      Throwable cause = error.getCause();
      if (cause instanceof RuntimeException) {
        throw (RuntimeException) cause;
      }
      throw error;
    }
  }

  private static Method lookupMethod(
      Class<?> ownerClass, String ownerName, String methodName, Class<?> returnType)
      throws NoSuchMethodException {
    MethodKey key = new MethodKey(ownerName, methodName, returnType);
    synchronized (METHOD_CACHE_LOCK) {
      Method cached = METHOD_CACHE.get(key);
      if (cached != null) {
        return cached;
      }
    }
    Method method = ownerClass.getMethod(methodName);
    if (method.getParameterTypes().length != 0) {
      throw new NoSuchMethodException(ownerName + "#" + methodName);
    }
    if (!ownerName.equals(method.getDeclaringClass().getName())) {
      throw new NoSuchMethodException(ownerName + "#" + methodName);
    }
    if (returnType != Object.class && !returnType.equals(method.getReturnType())) {
      throw new NoSuchMethodException(ownerName + "#" + methodName);
    }
    synchronized (METHOD_CACHE_LOCK) {
      METHOD_CACHE.put(key, method);
    }
    return method;
  }

  private static Class<?> classForName(String name) throws ClassNotFoundException {
    return Class.forName(name);
  }

  private static Field coordinateField(Class<?> ownerClass, String name)
      throws NoSuchFieldException {
    Field field = ownerClass.getField(name);
    int modifiers = field.getModifiers();
    if (!ownerClass.equals(field.getDeclaringClass())
        || !double.class.equals(field.getType())
        || !Modifier.isPublic(modifiers)
        || !Modifier.isFinal(modifiers)
        || Modifier.isStatic(modifiers)) {
      throw new NoSuchFieldException(ownerClass.getName() + "#" + name);
    }
    return field;
  }

  private static boolean isExact(Object value, String className) {
    return value != null && className.equals(value.getClass().getName());
  }

  private static String className(Object value) {
    return value == null ? "" : value.getClass().getName();
  }

  private static double number(Object value) {
    if (value instanceof Number) {
      return ((Number) value).doubleValue();
    }
    throw new IllegalArgumentException("not a number");
  }

  private static Integer integer(Object value) {
    if (value == null) {
      return null;
    }
    if (value instanceof Integer) {
      return (Integer) value;
    }
    throw new IllegalArgumentException("not an integer");
  }

  private static boolean bool(Object value) {
    if (value instanceof Boolean) {
      return ((Boolean) value).booleanValue();
    }
    throw new IllegalArgumentException("not a boolean");
  }

  private static boolean validDistance(double value, boolean allowZero) {
    return !Double.isNaN(value)
        && !Double.isInfinite(value)
        && (allowZero ? value >= 0.0 : value > 0.0)
        && value <= 2000000.0;
  }

  private static boolean validCoordinate(double value, double minimum, double maximum) {
    return !Double.isNaN(value)
        && !Double.isInfinite(value)
        && value >= minimum
        && value <= maximum;
  }

  private static Route invalidRoute(String outcome, int inputCount) {
    DIAGNOSTICS.record(outcome);
    return Route.absent(outcome, inputCount);
  }

  private static SafetySource invalidSafetySource(String outcome) {
    DIAGNOSTICS.record(outcome);
    return SafetySource.absent(outcome);
  }

  private static String enumName(Object value) {
    if (value instanceof Enum<?>) {
      return ((Enum<?>) value).name();
    }
    throw new IllegalArgumentException("not an enum");
  }

  private static String mapManeuver(Object value) {
    String name = enumName(value);
    if ("Left".equals(name)) {
      return "left";
    }
    if ("Right".equals(name)) {
      return "right";
    }
    if ("Direction11".equals(name)) {
      return "slight_left";
    }
    if ("RightDirection".equals(name)) {
      return "slight_right";
    }
    if ("UTurn".equals(name)) {
      return "u_turn";
    }
    if ("Goal".equals(name)) {
      return "arrive";
    }
    return null;
  }

  private static String cameraKind(String codeName) {
    if ("MoveSpeedCam".equals(codeName)) {
      return "mobile_camera";
    }
    if ("StartSectionSpeedCam".equals(codeName)
        || "EndSectionSpeedCam".equals(codeName)
        || "VariableSectionStart".equals(codeName)
        || "VariableSectionEnd".equals(codeName)) {
      return "section_camera";
    }
    return null;
  }

  private static double distanceFrom(Object distanceSource) throws ReflectiveOperationException {
    if (!isExact(distanceSource, DISTANCE)) {
      DIAGNOSTICS.record("safety_distance_descriptor");
      throw new NoSuchMethodException("distance descriptor");
    }
    double distanceM = number(invoke(distanceSource, DISTANCE, "getDistance", double.class));
    if (!validDistance(distanceM, false)) {
      DIAGNOSTICS.record("safety_distance");
      throw new IllegalArgumentException("distance");
    }
    return distanceM;
  }

  private static double speedBumpDistanceFrom(SafetySource source, Object displayCode) {
    if (source == null
        || !source.present
        || !source.speedBump
        || !enumName(displayCode).equals(source.codeName)
        || !validDistance(source.distanceM, false)) {
      DIAGNOSTICS.record("safety_source_code");
      throw new IllegalArgumentException("speed bump source code");
    }
    return source.distanceM;
  }

  private static String trimText(Object value) {
    if (!(value instanceof String)) {
      return "";
    }
    String text = (String) value;
    return text.length() <= MAX_TEXT ? text : text.substring(0, MAX_TEXT);
  }

  public static final class Guidance {
    public final boolean present;
    public final String maneuver;
    public final double distanceM;
    public final String roadName;
    public final String mainText;

    public Guidance(
        boolean present, String maneuver, double distanceM, String roadName, String mainText) {
      this.present = present;
      this.maneuver = maneuver == null ? "" : maneuver;
      this.distanceM = distanceM;
      this.roadName = roadName == null ? "" : roadName;
      this.mainText = mainText == null ? "" : mainText;
    }

    public static Guidance absent() {
      return new Guidance(false, "", 0.0, "", "");
    }
  }

  public static final class Safety {
    public final boolean present;
    public final String kind;
    public final double distanceM;
    public final int speedKph;

    public Safety(boolean present, String kind, double distanceM, int speedKph) {
      this.present = present;
      this.kind = kind == null ? "" : kind;
      this.distanceM = distanceM;
      this.speedKph = speedKph;
    }

    public static Safety absent() {
      return new Safety(false, "", 0.0, 0);
    }
  }

  public static final class SafetySource {
    public final boolean present;
    public final String codeName;
    public final boolean speedBump;
    public final double distanceM;
    public final String outcome;

    public SafetySource(
        boolean present,
        String codeName,
        boolean speedBump,
        double distanceM,
        String outcome) {
      this.present = present;
      this.codeName = codeName == null ? "" : codeName;
      this.speedBump = speedBump;
      this.distanceM = distanceM;
      this.outcome = outcome == null ? "" : outcome;
    }

    public static SafetySource absent() {
      return absent("");
    }

    public static SafetySource absent(String outcome) {
      return new SafetySource(false, "", false, 0.0, outcome);
    }
  }

  public static final class RoutePoint {
    public final double latitude;
    public final double longitude;

    public RoutePoint(double latitude, double longitude) {
      this.latitude = latitude;
      this.longitude = longitude;
    }
  }

  public static final class Route {
    public final boolean present;
    public final List<RoutePoint> points;
    public final int inputCount;
    public final int outputCount;
    public final String outcome;

    public Route(
        boolean present,
        List<RoutePoint> points,
        int inputCount,
        int outputCount,
        String outcome) {
      this.present = present;
      this.points = Collections.unmodifiableList(new ArrayList<RoutePoint>(points));
      this.inputCount = inputCount;
      this.outputCount = outputCount;
      this.outcome = outcome == null ? "" : outcome;
    }

    public static Route absent() {
      return absent("", 0);
    }

    public static Route absent(String outcome, int inputCount) {
      return new Route(false, Collections.<RoutePoint>emptyList(), inputCount, 0, outcome);
    }
  }

  public static final class MappedUpdate {
    public final String channel;
    public final String lifecycle;
    public final Guidance guidance;
    public final Safety safety;
    public final Route route;

    private MappedUpdate(
        String channel, String lifecycle, Guidance guidance, Safety safety, Route route) {
      this.channel = channel;
      this.lifecycle = lifecycle;
      this.guidance = guidance;
      this.safety = safety;
      this.route = route;
    }

    public static MappedUpdate status(String lifecycle) {
      return new MappedUpdate(
          "status", lifecycle, Guidance.absent(), Safety.absent(), Route.absent());
    }

    public static MappedUpdate current(Guidance guidance) {
      return new MappedUpdate(
          "tbt_current", "", guidance, Safety.absent(), Route.absent());
    }

    public static MappedUpdate next(Guidance guidance) {
      return new MappedUpdate(
          "tbt_next", "", guidance, Safety.absent(), Route.absent());
    }

    public static MappedUpdate safety(Safety safety) {
      return new MappedUpdate(
          "safety", "", Guidance.absent(), safety, Route.absent());
    }

    public static MappedUpdate route(Route route) {
      return new MappedUpdate(
          "route", "", Guidance.absent(), Safety.absent(), route);
    }
  }

  public static final class Diagnostics {
    private final int limit;
    private final List<String> codes = new ArrayList<String>();

    Diagnostics(int limit) {
      this.limit = limit;
    }

    synchronized void record(String code) {
      if (codes.size() < limit && !codes.contains(code)) {
        codes.add(code);
      }
    }

    public synchronized List<String> snapshotCodes() {
      return Collections.unmodifiableList(new ArrayList<String>(codes));
    }

    synchronized void clear() {
      codes.clear();
    }
  }

  private static final class MethodKey {
    private final String ownerName;
    private final String methodName;
    private final Class<?> returnType;

    MethodKey(String ownerName, String methodName, Class<?> returnType) {
      this.ownerName = ownerName == null ? "" : ownerName;
      this.methodName = methodName == null ? "" : methodName;
      this.returnType = returnType == null ? Object.class : returnType;
    }

    @Override
    public boolean equals(Object other) {
      if (!(other instanceof MethodKey)) {
        return false;
      }
      MethodKey key = (MethodKey) other;
      return ownerName.equals(key.ownerName)
          && methodName.equals(key.methodName)
          && returnType.equals(key.returnType);
    }

    @Override
    public int hashCode() {
      int result = ownerName.hashCode();
      result = 31 * result + methodName.hashCode();
      result = 31 * result + returnType.hashCode();
      return result;
    }
  }
}
