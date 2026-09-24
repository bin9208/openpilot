package ai.comma.naver.payload;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

final class PersistentNavigationTransport implements NavigationTransport {
  interface SocketFactory {
    Socket create() throws IOException;
  }

  private static final class Connection {
    final Socket socket;
    final BufferedWriter writer;

    Connection(Socket socket, BufferedWriter writer) {
      this.socket = socket;
      this.writer = writer;
    }
  }

  private final Object stateLock = new Object();
  private final String host;
  private final int port;
  private final int timeoutMillis;
  private final SocketFactory socketFactory;
  private boolean terminal;
  private Socket socket;
  private BufferedWriter writer;

  PersistentNavigationTransport(String host, int port, int timeoutMillis) {
    this(host, port, timeoutMillis, Socket::new);
  }

  PersistentNavigationTransport(
      String host, int port, int timeoutMillis, SocketFactory socketFactory) {
    if (!"127.0.0.1".equals(host)) {
      throw new IllegalArgumentException("diagnostic receiver must be loopback");
    }
    if (port != 7712 || timeoutMillis != 100 || socketFactory == null) {
      throw new IllegalArgumentException("unexpected diagnostic transport profile");
    }
    this.host = host;
    this.port = port;
    this.timeoutMillis = timeoutMillis;
    this.socketFactory = socketFactory;
  }

  @Override
  public void send(String line) throws IOException {
    Connection connection = connection();
    try {
      connection.writer.write(line);
      connection.writer.newLine();
      connection.writer.flush();
    } catch (IOException failure) {
      disconnect(connection.socket);
      throw failure;
    }
  }

  @Override
  public void close() {
    Socket active;
    synchronized (stateLock) {
      terminal = true;
      active = socket;
      socket = null;
      writer = null;
    }
    closeQuietly(active);
  }

  private Connection connection() throws IOException {
    synchronized (stateLock) {
      if (terminal) {
        throw new IOException("diagnostic transport is closed");
      }
      if (isOpen(socket)) {
        return new Connection(socket, writer);
      }
    }

    InetAddress loopback = InetAddress.getByAddress(new byte[] {127, 0, 0, 1});
    if (!loopback.getHostAddress().equals(host)) {
      throw new IOException("loopback resolution failed");
    }
    Socket next = socketFactory.create();
    try {
      next.connect(new InetSocketAddress(loopback, port), timeoutMillis);
      next.setSoTimeout(timeoutMillis);
      BufferedWriter nextWriter = new BufferedWriter(
          new OutputStreamWriter(next.getOutputStream(), StandardCharsets.UTF_8));
      Connection existing = null;
      boolean closed = false;
      synchronized (stateLock) {
        if (terminal) {
          closed = true;
        } else if (!isOpen(socket)) {
          socket = next;
          writer = nextWriter;
          return new Connection(next, nextWriter);
        } else {
          existing = new Connection(socket, writer);
        }
      }
      closeQuietly(next);
      if (closed) {
        throw new IOException("diagnostic transport is closed");
      }
      return existing;
    } catch (IOException | RuntimeException failure) {
      closeQuietly(next);
      throw failure;
    }
  }

  private void disconnect(Socket expected) {
    Socket active = null;
    synchronized (stateLock) {
      if (socket == expected) {
        active = socket;
        socket = null;
        writer = null;
      }
    }
    closeQuietly(active);
  }

  private static boolean isOpen(Socket candidate) {
    return candidate != null && candidate.isConnected() && !candidate.isClosed();
  }

  private static void closeQuietly(Socket candidate) {
    if (candidate == null) {
      return;
    }
    try {
      candidate.close();
    } catch (IOException ignored) {
      // Diagnostic shutdown is best effort and never exports error text.
    }
  }
}
