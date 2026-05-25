package ai.comma.galaxyhud;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

final class HudClient {
  interface Listener {
    void onTelemetry(HudTelemetry telemetry);
    void onConnectionState(String state, boolean connected);
  }

  private static final byte[] DISCOVERY_MAGIC = "galaxy-hud-discover-v1".getBytes(StandardCharsets.UTF_8);
  private static final int DEFAULT_PORT = 28888;
  private static final int DISCOVERY_PORT = 28889;

  private final Listener listener;
  private final String preferredEndpoint;
  private volatile boolean running;
  private Thread thread;

  HudClient(Listener listener, String preferredEndpoint) {
    this.listener = listener;
    this.preferredEndpoint = preferredEndpoint == null ? "" : preferredEndpoint.trim();
  }

  void start() {
    if (thread != null && thread.isAlive()) {
      return;
    }
    running = true;
    thread = new Thread(this::runLoop, "GalaxyHudClient");
    thread.start();
  }

  void stop() {
    running = false;
    if (thread != null) {
      thread.interrupt();
    }
  }

  private void runLoop() {
    while (running) {
      Set<InetSocketAddress> endpoints = new LinkedHashSet<>();
      addEndpoint(endpoints, preferredEndpoint);
      endpoints.addAll(discoverEndpoints());
      addEndpoint(endpoints, "127.0.0.1:" + DEFAULT_PORT);
      addEndpoint(endpoints, "192.168.42.2:" + DEFAULT_PORT);
      addEndpoint(endpoints, "192.168.42.129:" + DEFAULT_PORT);
      addEndpoint(endpoints, "192.168.43.1:" + DEFAULT_PORT);
      addEndpoint(endpoints, "192.168.49.1:" + DEFAULT_PORT);
      addEndpoint(endpoints, "10.0.2.2:" + DEFAULT_PORT);

      for (InetSocketAddress endpoint : endpoints) {
        if (!running) {
          return;
        }
        listener.onConnectionState("CONNECTING " + endpoint.getHostString() + ":" + endpoint.getPort(), false);
        if (readFrom(endpoint)) {
          break;
        }
      }
      sleep(900);
    }
  }

  private List<InetSocketAddress> discoverEndpoints() {
    ArrayList<InetSocketAddress> endpoints = new ArrayList<>();
    try (DatagramSocket socket = new DatagramSocket()) {
      socket.setBroadcast(true);
      socket.setSoTimeout(280);
      String[] broadcasts = {
          "255.255.255.255",
          "192.168.42.255",
          "192.168.43.255",
          "192.168.49.255",
          "172.20.10.15"
      };
      for (String host : broadcasts) {
        DatagramPacket packet = new DatagramPacket(
            DISCOVERY_MAGIC,
            DISCOVERY_MAGIC.length,
            new InetSocketAddress(host, DISCOVERY_PORT));
        socket.send(packet);
      }

      long deadline = System.currentTimeMillis() + 700;
      byte[] buffer = new byte[512];
      while (System.currentTimeMillis() < deadline) {
        DatagramPacket reply = new DatagramPacket(buffer, buffer.length);
        socket.receive(reply);
        String body = new String(reply.getData(), reply.getOffset(), reply.getLength(), StandardCharsets.UTF_8).trim();
        JSONObject json = new JSONObject(body);
        if ("galaxy-hud-announce".equals(json.optString("type"))) {
          int port = json.optInt("port", DEFAULT_PORT);
          endpoints.add(new InetSocketAddress(reply.getAddress().getHostAddress(), port));
        }
      }
    } catch (Exception ignored) {
      // Discovery is opportunistic; manual and common endpoints are tried next.
    }
    return endpoints;
  }

  private boolean readFrom(InetSocketAddress endpoint) {
    try (Socket socket = new Socket()) {
      socket.setTcpNoDelay(true);
      socket.setKeepAlive(true);
      socket.connect(endpoint, 1200);
      listener.onConnectionState("LIVE " + endpoint.getHostString() + ":" + endpoint.getPort(), true);

      BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8), 128 * 1024);
      String line;
      while (running && (line = reader.readLine()) != null) {
        if (line.isEmpty()) {
          continue;
        }
        JSONObject root = new JSONObject(line);
        if ("hello".equals(root.optString("type"))) {
          continue;
        }
        listener.onTelemetry(HudTelemetry.fromJson(root));
      }
      listener.onConnectionState("DISCONNECTED", false);
      return true;
    } catch (Exception e) {
      listener.onConnectionState("WAITING", false);
      return false;
    }
  }

  private static void addEndpoint(Set<InetSocketAddress> endpoints, String endpoint) {
    if (endpoint == null || endpoint.trim().isEmpty()) {
      return;
    }
    String value = endpoint.trim();
    String host = value;
    int port = DEFAULT_PORT;
    int colon = value.lastIndexOf(':');
    if (colon > 0 && colon < value.length() - 1) {
      host = value.substring(0, colon);
      try {
        port = Integer.parseInt(value.substring(colon + 1));
      } catch (NumberFormatException ignored) {
        port = DEFAULT_PORT;
      }
    }
    endpoints.add(new InetSocketAddress(host, port));
  }

  private static void sleep(long millis) {
    try {
      Thread.sleep(millis);
    } catch (InterruptedException ignored) {
      Thread.currentThread().interrupt();
    }
  }
}
