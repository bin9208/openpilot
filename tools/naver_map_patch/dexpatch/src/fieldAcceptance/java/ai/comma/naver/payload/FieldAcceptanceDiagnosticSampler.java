package ai.comma.naver.payload;

import java.lang.reflect.Array;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Field-only sampler variant that follows allowlisted enum classifiers. */
final class DiagnosticSampler {
  private static final String[] SENSITIVE_TOKENS = {
    "latitude", "longitude", "lat", "lon", "coord", "place", "account",
    "device", "road", "routepoint", "geometry", "token", "secret",
    "exception", "message", "serial", "address", "home"
  };

  private final Set<String> accessorAllowlist;
  private final int maxDepth;
  private final int maxAccessors;
  private final int maxCollectionItems;
  private final int maxSampleChars;
  private final int maxChannelChars;

  DiagnosticSampler(
      Set<String> accessorAllowlist,
      int maxDepth,
      int maxAccessors,
      int maxCollectionItems,
      int maxSampleChars,
      int maxChannelChars) {
    if (maxDepth != 4
        || maxAccessors != 16
        || maxCollectionItems != 4
        || maxSampleChars != 160
        || maxChannelChars != 4096) {
      throw new IllegalArgumentException("unexpected diagnostic sampling bounds");
    }
    this.accessorAllowlist = Collections.unmodifiableSet(
        new HashSet<>(accessorAllowlist));
    this.maxDepth = maxDepth;
    this.maxAccessors = maxAccessors;
    this.maxCollectionItems = maxCollectionItems;
    this.maxSampleChars = maxSampleChars;
    this.maxChannelChars = maxChannelChars;
  }

  static boolean acceptsChannelValue(String channel, Object value) {
    if (!("route".equals(channel) || "safety".equals(channel))) {
      return true;
    }
    return value != null
        && value.getClass() == ProductionRuntime.MappingOutcome.class;
  }

  String sample(Object value) {
    if (value != null
        && value.getClass() == ProductionRuntime.MappingOutcome.class) {
      return mappingOutcome((ProductionRuntime.MappingOutcome) value);
    }
    StringBuilder output = new StringBuilder(Math.min(maxChannelChars, 512));
    appendValue(output, value, 0);
    if (output.length() <= maxChannelChars) {
      return output.toString();
    }
    return "{\"descriptor\":\""
        + escape(descriptor(value == null ? Object.class : value.getClass()))
        + "\",\"truncated\":true}";
  }

  private static String mappingOutcome(ProductionRuntime.MappingOutcome value) {
    return "{\"channel\":\"" + escape(value.channel)
        + "\",\"result\":\"" + escape(value.result)
        + "\",\"root_descriptor\":\"" + escape(value.rootDescriptor)
        + "\",\"input_count\":" + value.inputCount
        + ",\"output_count\":" + value.outputCount
        + ",\"revision\":" + value.revision
        + ",\"item_present\":" + value.itemPresent
        + ",\"distance_valid\":" + value.distanceValid
        + ",\"frame_eligible\":" + value.frameEligible
        + "}";
  }

  private void appendValue(StringBuilder output, Object value, int depth) {
    if (value == null) {
      output.append("{\"kind\":\"null\"}");
      return;
    }
    Class<?> type = value.getClass();
    output.append("{\"descriptor\":\"").append(escape(descriptor(type))).append('"');
    if (value instanceof Enum<?>) {
      if (!isAsciiJavaIdentifier(((Enum<?>) value).name())) {
        throw new IllegalArgumentException("unsupported diagnostic enum name");
      }
      output.append(",\"enum_name\":\"")
          .append(escape(((Enum<?>) value).name()))
          .append('"');
      if (depth >= maxDepth) {
        output.append('}');
      } else {
        appendAccessors(output, value, type, depth);
      }
      return;
    }
    if (value instanceof CharSequence) {
      int length = Math.min(((CharSequence) value).length(), maxSampleChars);
      output.append(",\"string_length\":").append(length);
      if (((CharSequence) value).length() > maxSampleChars) {
        output.append(",\"truncated\":true");
      }
      output.append('}');
      return;
    }
    if (value instanceof Number) {
      output.append(",\"numeric_bucket\":\"")
          .append(numericBucket((Number) value))
          .append("\"}");
      return;
    }
    if (value instanceof Boolean) {
      output.append(",\"boolean\":").append(((Boolean) value).booleanValue()).append('}');
      return;
    }
    if (type.isArray() || value instanceof Collection<?>) {
      appendCollection(output, value, type);
      return;
    }
    if (depth >= maxDepth) {
      output.append(",\"depth_limited\":true}");
      return;
    }
    appendAccessors(output, value, type, depth);
  }

  private void appendCollection(StringBuilder output, Object value, Class<?> type) {
    int size = type.isArray() ? Array.getLength(value) : ((Collection<?>) value).size();
    int sampled = Math.min(size, maxCollectionItems);
    output.append(",\"collection_size\":").append(size)
        .append(",\"sampled_items\":").append(sampled)
        .append(",\"element_descriptors\":[");
    for (int index = 0; index < sampled; index++) {
      Object element = type.isArray()
          ? Array.get(value, index)
          : collectionElement((Collection<?>) value, index);
      if (index > 0) {
        output.append(',');
      }
      output.append('"')
          .append(escape(descriptor(element == null ? Object.class : element.getClass())))
          .append('"');
    }
    output.append("]}");
  }

  private static Object collectionElement(Collection<?> collection, int wanted) {
    int index = 0;
    for (Object element : collection) {
      if (index == wanted) {
        return element;
      }
      index += 1;
    }
    return null;
  }

  private void appendAccessors(
      StringBuilder output, Object value, Class<?> type, int depth) {
    List<Method> candidates = new ArrayList<>();
    for (Method method : type.getMethods()) {
      if (isInspectable(method) && !isSensitive(method.getName())) {
        if (!isAsciiJavaIdentifier(method.getName())) {
          throw new IllegalArgumentException("unsupported diagnostic accessor name");
        }
        candidates.add(method);
      }
    }
    candidates.sort(Comparator
        .comparing(Method::getName)
        .thenComparing(DiagnosticSampler::methodDescriptor));
    List<Method> selected = new ArrayList<>();
    for (Method method : candidates) {
      if (accessorAllowlist.contains(accessorKey(type, method))) {
        selected.add(method);
        if (selected.size() == maxAccessors) {
          break;
        }
      }
    }
    if (selected.size() < maxAccessors) {
      for (Method method : candidates) {
        if (!accessorAllowlist.contains(accessorKey(type, method))) {
          selected.add(method);
          if (selected.size() == maxAccessors) {
            break;
          }
        }
      }
    }
    selected.sort(Comparator
        .comparing(Method::getName)
        .thenComparing(DiagnosticSampler::methodDescriptor));
    output.append(",\"accessors\":[");
    for (int index = 0; index < selected.size(); index++) {
      Method method = selected.get(index);
      if (index > 0) {
        output.append(',');
      }
      String methodDescriptor = methodDescriptor(method);
      String allowlistKey = accessorKey(type, method);
      output.append("{\"name\":\"").append(escape(method.getName()))
          .append("\",\"descriptor\":\"").append(escape(methodDescriptor)).append('"');
      if (!accessorAllowlist.contains(allowlistKey)) {
        output.append(",\"status\":\"not_allowlisted\"}");
      } else {
        output.append(",\"value\":");
        appendAllowedValue(output, value, method, depth + 1);
        output.append('}');
      }
    }
    output.append("]}");
  }

  private static String accessorKey(Class<?> type, Method method) {
    return type.getName() + "#" + method.getName() + methodDescriptor(method);
  }

  private void appendAllowedValue(
      StringBuilder output, Object owner, Method method, int depth) {
    try {
      Object result = method.invoke(owner);
      appendValue(output, result, depth);
    } catch (ReflectiveOperationException | RuntimeException ignored) {
      output.append("{\"status\":\"accessor_error\"}");
    }
  }

  private static boolean isInspectable(Method method) {
    return Modifier.isPublic(method.getModifiers())
        && !Modifier.isStatic(method.getModifiers())
        && method.getParameterTypes().length == 0
        && method.getReturnType() != Void.TYPE
        && method.getDeclaringClass() != Object.class
        && !"toString".equals(method.getName())
        && !"hashCode".equals(method.getName());
  }

  private static boolean isSensitive(String name) {
    String lowered = name.toLowerCase();
    for (String token : SENSITIVE_TOKENS) {
      if (lowered.contains(token)) {
        return true;
      }
    }
    return false;
  }

  private static String numericBucket(Number number) {
    double value = number.doubleValue();
    if (Double.isNaN(value)) {
      return "nan";
    }
    if (Double.isInfinite(value)) {
      return value < 0 ? "negative_infinity" : "positive_infinity";
    }
    if (value == 0.0d) {
      return "zero";
    }
    double magnitude = Math.abs(value);
    String band;
    if (magnitude < 1.0d) {
      band = "lt_1";
    } else if (magnitude < 10.0d) {
      band = "1_to_10";
    } else if (magnitude < 100.0d) {
      band = "10_to_100";
    } else if (magnitude < 1000.0d) {
      band = "100_to_1000";
    } else {
      band = "gte_1000";
    }
    return value < 0 ? "negative_" + band : "positive_" + band;
  }

  private static String descriptor(Class<?> type) {
    String value;
    if (type.isPrimitive()) {
      if (type == Void.TYPE) {
        value = "V";
      } else if (type == Boolean.TYPE) {
        value = "Z";
      } else if (type == Byte.TYPE) {
        value = "B";
      } else if (type == Character.TYPE) {
        value = "C";
      } else if (type == Short.TYPE) {
        value = "S";
      } else if (type == Integer.TYPE) {
        value = "I";
      } else if (type == Long.TYPE) {
        value = "J";
      } else if (type == Float.TYPE) {
        value = "F";
      } else {
        value = "D";
      }
    } else if (type.isArray()) {
      value = type.getName().replace('.', '/');
    } else {
      value = "L" + type.getName().replace('.', '/') + ";";
    }
    if (!isAsciiDescriptor(value, true)) {
      throw new IllegalArgumentException("unsupported diagnostic descriptor");
    }
    return value;
  }

  private static String methodDescriptor(Method method) {
    StringBuilder descriptor = new StringBuilder("(");
    for (Class<?> parameter : method.getParameterTypes()) {
      descriptor.append(descriptor(parameter));
    }
    String value = descriptor.append(')')
        .append(descriptor(method.getReturnType()))
        .toString();
    if (value.length() > 512
        || !value.matches("\\(\\)(?:\\[*[ZBCSIJFD]|\\[*L[A-Za-z0-9_$/]+;)")) {
      throw new IllegalArgumentException("unsupported diagnostic method descriptor");
    }
    return value;
  }

  private static boolean isAsciiJavaIdentifier(String value) {
    return value != null && value.matches("[A-Za-z_$][A-Za-z0-9_$]{0,159}");
  }

  private static boolean isAsciiDescriptor(String value, boolean allowVoid) {
    return value != null
        && value.length() <= 512
        && (value.matches("\\[*[ZBCSIJFD]")
            || (allowVoid && "V".equals(value))
            || value.matches("\\[*L[A-Za-z0-9_$/]+;"));
  }

  private static String escape(String value) {
    StringBuilder escaped = new StringBuilder(value.length());
    for (int index = 0; index < value.length(); index++) {
      char character = value.charAt(index);
      switch (character) {
        case '"':
          escaped.append("\\\"");
          break;
        case '\\':
          escaped.append("\\\\");
          break;
        case '\n':
          escaped.append("\\n");
          break;
        case '\r':
          escaped.append("\\r");
          break;
        case '\t':
          escaped.append("\\t");
          break;
        default:
          if (character < 0x20) {
            escaped.append('?');
          } else {
            escaped.append(character);
          }
      }
    }
    return escaped.toString();
  }
}
