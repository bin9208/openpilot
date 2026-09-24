package ai.comma.naver.payload;

import java.io.IOException;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.channels.WritableByteChannel;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

public class NaverNavigationSender {
  public interface Connector {
    OutputStream connect() throws IOException;
  }

  public interface Clock {
    long nowMs();
  }

  private static final int MAX_FRAME_BYTES = 262144;
  private static final int WRITE_TIMEOUT_MS = 500;
  private static final long HEARTBEAT_MS = 500L;
  private static final long MIN_BACKOFF_MS = 1000L;
  private static final long MAX_BACKOFF_MS = 30000L;
  private final Connector connector;
  private final Clock clock;
  private NaverNavigationState latest;
  private NaverNavigationState terminal;
  private OutputStream output;
  private long lastFullSentMs = Long.MIN_VALUE;
  private long nextConnectMs;
  private long reconnectBackoffMs = MIN_BACKOFF_MS;
  private String sendSessionId;
  private long sendSequence;
  private String lastFullSessionId;
  private long latestRevision;
  private long sentLatestRevision;
  private volatile boolean running;
  private Thread worker;
  private boolean sending;

  public NaverNavigationSender(Connector connector, Clock clock) {
    this.connector = connector;
    this.clock = clock;
  }

  public static NaverNavigationSender createDefault() {
    NaverNavigationSender sender = new NaverNavigationSender(
        DiscoveryTcpConnector.createDefault(), new SystemClock());
    sender.start();
    return sender;
  }

  public static NaverNavigationSender noopForTesting() {
    return new NaverNavigationSender(new Connector() {
      @Override
      public OutputStream connect() {
        return new OutputStream() {
          @Override
          public void write(int b) {
          }
        };
      }
    }, new SystemClock());
  }

  public synchronized void offer(NaverNavigationState state) {
    if (state == null || !state.isFrameEligible()) {
      return;
    }
    if (state.isTerminal()) {
      terminal = state;
    } else {
      latest = state;
      if (latestRevision < Long.MAX_VALUE) {
        latestRevision++;
      }
    }
    notifyAll();
  }

  public synchronized void start() {
    if (running) {
      return;
    }
    running = true;
    worker = new Thread(new Runnable() {
      @Override
      public void run() {
        while (running) {
          flushOnce();
          synchronized (NaverNavigationSender.this) {
            try {
              NaverNavigationSender.this.wait(nextWaitMsLocked());
            } catch (InterruptedException ignored) {
            }
          }
        }
      }
    }, "naver-production-sender");
    worker.setDaemon(true);
    worker.start();
  }

  public void flushOnceForTesting() {
    flushOnce();
  }

  void flushOnce() {
    PendingFrame pending = selectPendingFrame();
    if (pending == null) {
      return;
    }
    boolean sent = send(pending.state, pending.nowMs);
    synchronized (this) {
      sending = false;
      if (sent) {
        if (pending.terminal) {
          if (terminal == pending.state) {
            terminal = null;
          }
          if (sameSession(latest, pending.state)) {
            latest = null;
          }
        } else {
          sentLatestRevision = Math.max(sentLatestRevision, pending.latestRevision);
          lastFullSessionId = pending.state.sessionId;
          lastFullSentMs = pending.nowMs;
        }
      }
      notifyAll();
    }
  }

  public synchronized int pendingSlotCountForTesting() {
    int count = 0;
    if (latest != null) {
      count++;
    }
    if (terminal != null) {
      count++;
    }
    return count;
  }

  public synchronized long reconnectBackoffMsForTesting() {
    return reconnectBackoffMs;
  }

  private PendingFrame selectPendingFrame() {
    synchronized (this) {
      if (sending) {
        return null;
      }
      long now = clock.nowMs();
      if (terminal != null) {
        if (now < nextConnectMs) {
          return null;
        }
        sending = true;
        return new PendingFrame(terminal, now, true, latestRevision);
      }
      if (latest == null) {
        return null;
      }
      boolean newSession = !latest.sessionId.equals(lastFullSessionId);
      boolean dirty = latestRevision != sentLatestRevision;
      if (!newSession
          && !dirty
          && lastFullSentMs != Long.MIN_VALUE
          && now - lastFullSentMs < HEARTBEAT_MS) {
        return null;
      }
      if (now < nextConnectMs) {
        return null;
      }
      sending = true;
      return new PendingFrame(latest, now, false, latestRevision);
    }
  }

  private long nextWaitMsLocked() {
    long now = clock.nowMs();
    if (terminal != null) {
      return boundedPositiveDelay(nextConnectMs > now ? nextConnectMs - now : 1L);
    }
    if (latest == null) {
      return HEARTBEAT_MS;
    }
    if (nextConnectMs > now) {
      return boundedPositiveDelay(nextConnectMs - now);
    }
    boolean newSession = !latest.sessionId.equals(lastFullSessionId);
    boolean dirty = latestRevision != sentLatestRevision;
    if (newSession || dirty || lastFullSentMs == Long.MIN_VALUE) {
      return 1L;
    }
    return boundedPositiveDelay(HEARTBEAT_MS - (now - lastFullSentMs));
  }

  private static long boundedPositiveDelay(long delayMs) {
    if (delayMs <= 0L) {
      return 1L;
    }
    return Math.min(delayMs, HEARTBEAT_MS);
  }

  private boolean send(NaverNavigationState state, long now) {
    long sequence = candidateSequence(state);
    NaverNavigationState transmitted = state.forTransmission(sequence, now);
    String json = NaverNavigationEnvelope.toJson(transmitted);
    if (json.isEmpty()) {
      return false;
    }
    byte[] frame = frameBytes(json);
    if (frameTooLarge(frame)) {
      return false;
    }
    try {
      OutputStream stream = stream(now);
      if (stream == null) {
        return false;
      }
      stream.write(frame);
      stream.flush();
      commitSequence(state, sequence);
      reconnectBackoffMs = MIN_BACKOFF_MS;
      return true;
    } catch (IOException error) {
      closeOutput();
      scheduleReconnect(now);
      return false;
    }
  }

  static boolean frameTooLarge(byte[] frame) {
    return frame != null && frame.length > MAX_FRAME_BYTES;
  }

  private static byte[] frameBytes(String json) {
    return (json + "\n").getBytes(StandardCharsets.UTF_8);
  }

  private OutputStream stream(long now) throws IOException {
    if (output != null) {
      return output;
    }
    if (now < nextConnectMs) {
      return null;
    }
    try {
      output = connector.connect();
      return output;
    } catch (IOException error) {
      throw error;
    }
  }

  private void scheduleReconnect(long now) {
    nextConnectMs = now + reconnectBackoffMs;
    if (reconnectBackoffMs < MAX_BACKOFF_MS) {
      reconnectBackoffMs = Math.min(MAX_BACKOFF_MS, reconnectBackoffMs * 2L);
    }
  }

  private void closeOutput() {
    if (output != null) {
      try {
        output.close();
      } catch (IOException ignored) {
      }
      output = null;
    }
  }

  private long candidateSequence(NaverNavigationState state) {
    if (!state.sessionId.equals(sendSessionId)) {
      return Math.max(1L, state.sequence);
    }
    if (sendSequence < Long.MAX_VALUE) {
      return sendSequence + 1L;
    }
    return Long.MAX_VALUE;
  }

  private void commitSequence(NaverNavigationState state, long sequence) {
    sendSessionId = state.sessionId;
    sendSequence = sequence;
  }

  private static boolean sameSession(NaverNavigationState left, NaverNavigationState right) {
    return left != null && right != null && left.sessionId.equals(right.sessionId);
  }

  private static final class PendingFrame {
    final NaverNavigationState state;
    final long nowMs;
    final boolean terminal;
    final long latestRevision;

    PendingFrame(
        NaverNavigationState state, long nowMs, boolean terminal, long latestRevision) {
      this.state = state;
      this.nowMs = nowMs;
      this.terminal = terminal;
      this.latestRevision = latestRevision;
    }
  }

  interface WriteWaiter extends AutoCloseable {
    boolean awaitWritable(long timeoutMs) throws IOException;
    boolean isOpen();
    @Override
    void close() throws IOException;
  }

  static final class BoundedChannelOutputStream extends OutputStream {
    private final WritableByteChannel channel;
    private final WriteWaiter waiter;
    private final int timeoutMs;

    BoundedChannelOutputStream(
        WritableByteChannel channel, WriteWaiter waiter, int timeoutMs) {
      if (channel == null || waiter == null || timeoutMs <= 0) {
        throw new IllegalArgumentException("bounded channel output configuration is invalid");
      }
      this.channel = channel;
      this.waiter = waiter;
      this.timeoutMs = timeoutMs;
    }

    @Override
    public void write(int value) throws IOException {
      write(new byte[] {(byte) value}, 0, 1);
    }

    @Override
    public void write(byte[] bytes, int offset, int length) throws IOException {
      if (bytes == null) {
        throw new NullPointerException("bytes");
      }
      if (offset < 0 || length < 0 || offset > bytes.length - length) {
        throw new IndexOutOfBoundsException();
      }
      if (length == 0) {
        return;
      }
      ByteBuffer buffer = ByteBuffer.wrap(bytes, offset, length);
      long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
      try {
        while (buffer.hasRemaining()) {
          int written = channel.write(buffer);
          if (written < 0) {
            throw new IOException("navigation TCP channel closed during write");
          }
          if (written > 0) {
            continue;
          }
          long remainingNanos = deadline - System.nanoTime();
          if (remainingNanos <= 0L) {
            closeQuietly();
            throw new SocketTimeoutException("navigation TCP write timed out");
          }
          boolean writable = waiter.awaitWritable(Math.max(
              1L, TimeUnit.NANOSECONDS.toMillis(remainingNanos)));
          if (!writable && deadline - System.nanoTime() <= 0L) {
            closeQuietly();
            throw new SocketTimeoutException("navigation TCP write timed out");
          }
        }
      } catch (IOException error) {
        closeQuietly();
        throw error;
      }
    }

    @Override
    public void flush() {
      // SocketChannel writes have no separate user-space flush buffer.
    }

    @Override
    public void close() throws IOException {
      IOException failure = null;
      try {
        waiter.close();
      } catch (IOException error) {
        failure = error;
      }
      try {
        channel.close();
      } catch (IOException error) {
        if (failure == null) {
          failure = error;
        }
      }
      if (failure != null) {
        throw failure;
      }
    }

    private void closeQuietly() {
      try {
        close();
      } catch (IOException ignored) {
      }
    }
  }

  private static final class SelectorWriteWaiter implements WriteWaiter {
    private final Selector selector;

    SelectorWriteWaiter(SocketChannel channel) throws IOException {
      selector = Selector.open();
      channel.register(selector, SelectionKey.OP_WRITE);
    }

    @Override
    public boolean awaitWritable(long timeoutMs) throws IOException {
      int ready = selector.select(timeoutMs);
      selector.selectedKeys().clear();
      return ready > 0;
    }

    @Override
    public boolean isOpen() {
      return selector.isOpen();
    }

    @Override
    public void close() throws IOException {
      selector.close();
    }
  }

  static final class DiscoveryBindCandidate {
    final String interfaceName;
    final InetAddress address;
    final InetAddress broadcast;

    DiscoveryBindCandidate(
        String interfaceName, InetAddress address, InetAddress broadcast) {
      this.interfaceName = interfaceName == null ? "" : interfaceName;
      this.address = address;
      this.broadcast = broadcast;
    }
  }

  static final class DiscoveryTcpConnector implements Connector {
    private static final String REQUEST =
        "{\"type\":\"carrot.navigation.discover\",\"source\":\"naver\",\"schema_version\":1}";
    private static final String RESPONSE_TYPE = "carrot.navigation.discover.response";
    private static final int EXPECTED_LEASE_MS = 2000;
    private final String discoveryHost;
    private final int discoveryPort;
    private final int responsePort;
    private final int tcpPort;
    private final int timeoutMs;
    private final boolean enumerateInterfaceBroadcasts;

    private DiscoveryTcpConnector(
        String discoveryHost,
        int discoveryPort,
        int responsePort,
        int tcpPort,
        int timeoutMs,
        boolean enumerateInterfaceBroadcasts) {
      this.discoveryHost = discoveryHost;
      this.discoveryPort = discoveryPort;
      this.responsePort = responsePort;
      this.tcpPort = tcpPort;
      this.timeoutMs = timeoutMs;
      this.enumerateInterfaceBroadcasts = enumerateInterfaceBroadcasts;
    }

    static DiscoveryTcpConnector createDefault() {
      return new DiscoveryTcpConnector(
          "255.255.255.255", 7706, 7705, 7712, 500, true);
    }

    static DiscoveryTcpConnector forTesting(
        String discoveryHost,
        int discoveryPort,
        int responsePort,
        int tcpPort,
        int timeoutMs) {
      return new DiscoveryTcpConnector(
          discoveryHost, discoveryPort, responsePort, tcpPort, timeoutMs, false);
    }

    @Override
    public OutputStream connect() throws IOException {
      InetSocketAddress endpoint = discover();
      SocketChannel channel = SocketChannel.open();
      Socket socket = channel.socket();
      try {
        socket.setKeepAlive(true);
        socket.connect(endpoint, timeoutMs);
        channel.configureBlocking(false);
        BoundedChannelOutputStream output = new BoundedChannelOutputStream(
            channel, new SelectorWriteWaiter(channel), WRITE_TIMEOUT_MS);
        trace("tcp_connected");
        return output;
      } catch (IOException error) {
        trace("tcp_connect_failed");
        try {
          channel.close();
        } catch (IOException ignored) {
        }
        throw error;
      }
    }

    private InetSocketAddress discover() throws IOException {
      byte[] request = REQUEST.getBytes(StandardCharsets.UTF_8);
      Set<InetAddress> forbiddenResponders = new LinkedHashSet<InetAddress>();
      DiscoveryBindCandidate bindCandidate = discoveryBindCandidate();
      Set<InetAddress> destinations = discoveryDestinations(
          forbiddenResponders, bindCandidate);
      InetAddress bindAddress = bindCandidate == null ? null : bindCandidate.address;
      try (DatagramSocket socket = new DatagramSocket(null)) {
        socket.setReuseAddress(true);
        socket.bind(new InetSocketAddress(bindAddress, responsePort));
        trace(bindAddress == null ? "bind_wildcard" : "bind_site_local");
        socket.setBroadcast(true);
        socket.setSoTimeout(timeoutMs);
        IOException lastSendError = null;
        boolean requestSent = false;
        for (InetAddress destination : destinations) {
          try {
            socket.send(new DatagramPacket(request, request.length, destination, discoveryPort));
            requestSent = true;
          } catch (IOException error) {
            lastSendError = error;
          }
        }
        if (!requestSent) {
          throw lastSendError != null
              ? lastSendError : new IOException("no navigation discovery destination");
        }
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        byte[] response = new byte[1024];
        while (System.nanoTime() < deadline) {
          long remainingNanos = deadline - System.nanoTime();
          socket.setSoTimeout((int) Math.max(
              1L, TimeUnit.NANOSECONDS.toMillis(remainingNanos)));
          DatagramPacket packet = new DatagramPacket(response, response.length);
          try {
            socket.receive(packet);
          } catch (SocketTimeoutException error) {
            break;
          }
          String candidate = new String(
              packet.getData(), packet.getOffset(), packet.getLength(), StandardCharsets.UTF_8);
          InetSocketAddress endpoint =
              parseResponse(
                  candidate, packet.getAddress(), packet.getPort(), forbiddenResponders);
          if (endpoint != null) {
            return endpoint;
          }
        }
      }
      trace("discovery_timeout");
      throw new IOException("typed navigation discovery timed out");
    }

    private Set<InetAddress> discoveryDestinations(
        Set<InetAddress> forbiddenResponders, DiscoveryBindCandidate bindCandidate)
        throws IOException {
      Set<InetAddress> destinations = new LinkedHashSet<InetAddress>();
      if (bindCandidate != null && bindCandidate.broadcast instanceof Inet4Address) {
        destinations.add(bindCandidate.broadcast);
      }
      if (enumerateInterfaceBroadcasts) {
        try {
          Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
          while (interfaces != null && interfaces.hasMoreElements()) {
            NetworkInterface network = interfaces.nextElement();
            try {
              if (!network.isUp() || network.isLoopback()) {
                continue;
              }
              for (InterfaceAddress address : network.getInterfaceAddresses()) {
                InetAddress subnetNetwork = subnetNetworkAddress(
                    address.getAddress(), address.getNetworkPrefixLength());
                if (subnetNetwork != null) {
                  forbiddenResponders.add(subnetNetwork);
                }
                InetAddress broadcast = address.getBroadcast();
                if (broadcast instanceof Inet4Address) {
                  destinations.add(broadcast);
                  forbiddenResponders.add(broadcast);
                }
              }
            } catch (SocketException ignored) {
            }
          }
        } catch (SocketException ignored) {
        }
      }
      destinations.add(InetAddress.getByName(discoveryHost));
      return destinations;
    }

    private DiscoveryBindCandidate discoveryBindCandidate() {
      if (!enumerateInterfaceBroadcasts) {
        return null;
      }
      List<DiscoveryBindCandidate> candidates = new ArrayList<DiscoveryBindCandidate>();
      try {
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        while (interfaces != null && interfaces.hasMoreElements()) {
          NetworkInterface network = interfaces.nextElement();
          try {
            if (!network.isUp() || network.isLoopback()) {
              continue;
            }
            for (InterfaceAddress address : network.getInterfaceAddresses()) {
              if (address.getAddress() instanceof Inet4Address
                  && address.getBroadcast() instanceof Inet4Address) {
                candidates.add(new DiscoveryBindCandidate(
                    network.getName(), address.getAddress(), address.getBroadcast()));
              }
            }
          } catch (SocketException ignored) {
          }
        }
      } catch (SocketException ignored) {
      }
      return preferredBindCandidate(candidates);
    }

    static DiscoveryBindCandidate preferredBindCandidate(
        List<DiscoveryBindCandidate> candidates) {
      if (candidates == null) {
        return null;
      }
      DiscoveryBindCandidate preferred = null;
      for (DiscoveryBindCandidate candidate : candidates) {
        if (!isUsableBindCandidate(candidate)) {
          continue;
        }
        if (preferred == null || compareBindCandidates(candidate, preferred) < 0) {
          preferred = candidate;
        }
      }
      return preferred;
    }

    private static boolean isUsableBindCandidate(DiscoveryBindCandidate candidate) {
      if (candidate == null
          || !(candidate.address instanceof Inet4Address)
          || !(candidate.broadcast instanceof Inet4Address)) {
        return false;
      }
      InetAddress address = candidate.address;
      return address.isSiteLocalAddress()
          && !address.isAnyLocalAddress()
          && !address.isLoopbackAddress()
          && !address.isLinkLocalAddress()
          && !address.isMulticastAddress();
    }

    private static int compareBindCandidates(
        DiscoveryBindCandidate left, DiscoveryBindCandidate right) {
      int priority = Integer.compare(
          transportPriority(left.interfaceName), transportPriority(right.interfaceName));
      if (priority != 0) {
        return priority;
      }
      int name = left.interfaceName.compareTo(right.interfaceName);
      if (name != 0) {
        return name;
      }
      return left.address.getHostAddress().compareTo(right.address.getHostAddress());
    }

    private static int transportPriority(String interfaceName) {
      String name = interfaceName == null
          ? "" : interfaceName.toLowerCase(java.util.Locale.ROOT);
      if (name.startsWith("wlan") || name.startsWith("wifi")) {
        return 0;
      }
      if (name.startsWith("eth") || name.startsWith("en")
          || name.startsWith("usb") || name.startsWith("rndis")) {
        return 1;
      }
      if (name.startsWith("rmnet") || name.startsWith("ccmni")
          || name.startsWith("pdp")) {
        return 4;
      }
      if (name.startsWith("tun") || name.startsWith("tap")
          || name.startsWith("ppp") || name.startsWith("wg")) {
        return 5;
      }
      return 2;
    }

    static InetAddress subnetNetworkAddress(InetAddress address, short prefixLength) {
      if (!(address instanceof Inet4Address) || prefixLength < 0 || prefixLength > 32) {
        return null;
      }
      byte[] network = address.getAddress().clone();
      int remainingPrefixBits = prefixLength;
      for (int index = 0; index < network.length; index++) {
        int bytePrefixBits = Math.min(remainingPrefixBits, 8);
        int mask = bytePrefixBits == 0 ? 0 : (0xff << (8 - bytePrefixBits)) & 0xff;
        network[index] = (byte) ((network[index] & 0xff) & mask);
        remainingPrefixBits -= bytePrefixBits;
      }
      try {
        return InetAddress.getByAddress(network);
      } catch (IOException impossibleLength) {
        return null;
      }
    }

    InetSocketAddress parseResponse(String candidate, InetAddress packetAddress, int packetPort) {
      return parseResponse(
          candidate, packetAddress, packetPort, Collections.<InetAddress>emptySet());
    }

    InetSocketAddress parseResponse(
        String candidate,
        InetAddress packetAddress,
        int packetPort,
        Set<InetAddress> forbiddenResponders) {
      if (!isSafeResponder(packetAddress, packetPort, forbiddenResponders)) {
        trace("response_unsafe_endpoint");
        return null;
      }
      Map<String, JsonScalar> fields = FlatJsonParser.parse(candidate);
      if (fields == null || fields.size() != 6) {
        trace("response_invalid_shape");
        return null;
      }
      JsonScalar type = fields.get("type");
      JsonScalar server = fields.get("server");
      Long port = integer(fields.get("port"));
      Long schema = integer(fields.get("schema"));
      Long schemaVersion = integer(fields.get("schema_version"));
      Long leaseMs = integer(fields.get("lease_ms"));
      if (type == null || type.kind != JsonScalar.STRING || !RESPONSE_TYPE.equals(type.value)) {
        trace("response_type_mismatch");
        return null;
      }
      if (server == null || server.kind != JsonScalar.STRING
          || !packetAddress.getHostAddress().equals(server.value)) {
        trace("response_server_mismatch");
        return null;
      }
      if (port == null || port.longValue() != tcpPort
          || schema == null || schema.longValue() != 1L
          || schemaVersion == null || schemaVersion.longValue() != 1L
          || leaseMs == null || leaseMs.longValue() != EXPECTED_LEASE_MS) {
        trace("response_contract_mismatch");
        return null;
      }
      trace("response_accepted");
      return new InetSocketAddress(packetAddress, tcpPort);
    }

    private static void trace(String code) {
      try {
        Class<?> androidLog = Class.forName("android.util.Log");
        androidLog.getMethod("w", String.class, String.class)
            .invoke(null, "NAVER_TRANSPORT", code);
        return;
      } catch (ReflectiveOperationException | LinkageError | RuntimeException ignored) {
      }
      System.err.println("NAVER_TRANSPORT " + code);
    }

    private boolean isSafeResponder(
        InetAddress address, int packetPort, Set<InetAddress> forbiddenResponders) {
      if (packetPort != discoveryPort
          || !(address instanceof Inet4Address)
          || forbiddenResponders.contains(address)
          || address.isAnyLocalAddress()
          || address.isLinkLocalAddress()
          || address.isMulticastAddress()
          || isReservedOrBroadcast(address)) {
        return false;
      }
      return !enumerateInterfaceBroadcasts || !address.isLoopbackAddress();
    }

    private static boolean isReservedOrBroadcast(InetAddress address) {
      byte[] bytes = address.getAddress();
      if (bytes.length != 4) {
        return true;
      }
      int first = bytes[0] & 0xff;
      return first == 0 || first >= 240;
    }

    private static Long integer(JsonScalar scalar) {
      if (scalar == null || scalar.kind != JsonScalar.NUMBER) {
        return null;
      }
      try {
        return Long.valueOf(scalar.value);
      } catch (NumberFormatException error) {
        return null;
      }
    }

    private static final class JsonScalar {
      static final char STRING = 's';
      static final char NUMBER = 'n';
      static final char LITERAL = 'l';

      final char kind;
      final String value;

      JsonScalar(char kind, String value) {
        this.kind = kind;
        this.value = value;
      }
    }

    private static final class FlatJsonParser {
      private final String input;
      private int index;

      private FlatJsonParser(String input) {
        this.input = input;
      }

      static Map<String, JsonScalar> parse(String input) {
        if (input == null) {
          return null;
        }
        try {
          return new FlatJsonParser(input).parseObject();
        } catch (IllegalArgumentException error) {
          return null;
        }
      }

      private Map<String, JsonScalar> parseObject() {
        Map<String, JsonScalar> fields = new HashMap<String, JsonScalar>();
        whitespace();
        require('{');
        whitespace();
        if (consume('}')) {
          finish();
          return fields;
        }
        while (true) {
          whitespace();
          String key = string();
          if (fields.containsKey(key)) {
            throw new IllegalArgumentException("duplicate JSON key");
          }
          whitespace();
          require(':');
          whitespace();
          fields.put(key, scalar());
          whitespace();
          if (consume('}')) {
            finish();
            return fields;
          }
          require(',');
        }
      }

      private JsonScalar scalar() {
        if (peek('"')) {
          return new JsonScalar(JsonScalar.STRING, string());
        }
        if (peek('-') || digit(peek())) {
          return new JsonScalar(JsonScalar.NUMBER, number());
        }
        String[] literals = new String[] {"true", "false", "null"};
        for (String literal : literals) {
          if (input.startsWith(literal, index)) {
            index += literal.length();
            return new JsonScalar(JsonScalar.LITERAL, literal);
          }
        }
        throw new IllegalArgumentException("unsupported JSON value");
      }

      private String string() {
        require('"');
        StringBuilder value = new StringBuilder();
        while (index < input.length()) {
          char current = input.charAt(index++);
          if (current == '"') {
            return value.toString();
          }
          if (current < 0x20) {
            throw new IllegalArgumentException("JSON control character");
          }
          if (current != '\\') {
            value.append(current);
            continue;
          }
          if (index >= input.length()) {
            throw new IllegalArgumentException("truncated JSON escape");
          }
          char escaped = input.charAt(index++);
          switch (escaped) {
            case '"':
            case '\\':
            case '/':
              value.append(escaped);
              break;
            case 'b': value.append('\b'); break;
            case 'f': value.append('\f'); break;
            case 'n': value.append('\n'); break;
            case 'r': value.append('\r'); break;
            case 't': value.append('\t'); break;
            case 'u':
              if (index + 4 > input.length()) {
                throw new IllegalArgumentException("truncated JSON unicode escape");
              }
              try {
                value.append((char) Integer.parseInt(input.substring(index, index + 4), 16));
              } catch (NumberFormatException error) {
                throw new IllegalArgumentException("invalid JSON unicode escape");
              }
              index += 4;
              break;
            default:
              throw new IllegalArgumentException("invalid JSON escape");
          }
        }
        throw new IllegalArgumentException("unterminated JSON string");
      }

      private String number() {
        int start = index;
        consume('-');
        if (consume('0')) {
          if (digit(peek())) {
            throw new IllegalArgumentException("invalid leading zero");
          }
        } else {
          if (!nonzero(peek())) {
            throw new IllegalArgumentException("invalid JSON number");
          }
          while (digit(peek())) {
            index++;
          }
        }
        if (consume('.')) {
          digitsRequired();
        }
        if (consume('e') || consume('E')) {
          if (!consume('+')) {
            consume('-');
          }
          digitsRequired();
        }
        return input.substring(start, index);
      }

      private void digitsRequired() {
        if (!digit(peek())) {
          throw new IllegalArgumentException("invalid JSON number");
        }
        while (digit(peek())) {
          index++;
        }
      }

      private void whitespace() {
        while (index < input.length()) {
          char current = input.charAt(index);
          if (current != ' ' && current != '\t' && current != '\r' && current != '\n') {
            return;
          }
          index++;
        }
      }

      private void finish() {
        whitespace();
        if (index != input.length()) {
          throw new IllegalArgumentException("trailing JSON data");
        }
      }

      private void require(char expected) {
        if (!consume(expected)) {
          throw new IllegalArgumentException("unexpected JSON token");
        }
      }

      private boolean consume(char expected) {
        if (peek(expected)) {
          index++;
          return true;
        }
        return false;
      }

      private boolean peek(char expected) {
        return index < input.length() && input.charAt(index) == expected;
      }

      private char peek() {
        return index < input.length() ? input.charAt(index) : '\0';
      }

      private static boolean digit(char value) {
        return value >= '0' && value <= '9';
      }

      private static boolean nonzero(char value) {
        return value >= '1' && value <= '9';
      }
    }
  }

  private static final class SystemClock implements Clock {
    @Override
    public long nowMs() {
      return System.nanoTime() / 1000000L;
    }
  }
}
