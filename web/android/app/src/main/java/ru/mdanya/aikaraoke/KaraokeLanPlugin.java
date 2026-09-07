package ru.mdanya.aikaraoke;

import android.Manifest;
import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.view.WindowManager;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.PermissionState;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

@CapacitorPlugin(name = "KaraokeLan", permissions = {
    @Permission(alias = "downloads", strings = {Manifest.permission.WRITE_EXTERNAL_STORAGE})
})
public class KaraokeLanPlugin extends Plugin {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final AtomicBoolean scanning = new AtomicBoolean(false);

    @PluginMethod
    public void discover(PluginCall call) {
        if (!scanning.compareAndSet(false, true)) {call.reject("Поиск уже выполняется"); return;}
        worker.execute(() -> {
            Map<String, JSObject> found = new ConcurrentHashMap<>();
            Set<String> candidates = new LinkedHashSet<>();
            Set<InetAddress> broadcasts = new HashSet<>();
            try {
                broadcasts.add(InetAddress.getByName("255.255.255.255"));
                for (NetworkInterface iface : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                    if (!iface.isUp() || iface.isLoopback()) continue;
                    for (InterfaceAddress address : iface.getInterfaceAddresses()) {
                        if (!(address.getAddress() instanceof Inet4Address)) continue;
                        if (address.getBroadcast() != null) broadcasts.add(address.getBroadcast());
                        byte[] bytes = address.getAddress().getAddress();
                        String prefix = (bytes[0] & 255) + "." + (bytes[1] & 255) + "." + (bytes[2] & 255) + ".";
                        // Broadcast covers the actual subnet; bounded HTTP sweep covers nearby hosts
                        // when Wi-Fi equipment filters UDP. Manual names/IPs also support routed LANs.
                        if (address.getAddress().isSiteLocalAddress() || address.getAddress().isLinkLocalAddress())
                            for (int i = 1; i < 255; i++) candidates.add(prefix + i);
                    }
                }
                try (DatagramSocket socket = new DatagramSocket()) {
                    socket.setBroadcast(true); socket.setSoTimeout(200);
                    byte[] request = "AI_KARAOKE_DISCOVER_V1".getBytes(StandardCharsets.UTF_8);
                    for (int attempt = 0; attempt < 2; attempt++) {
                        for (InetAddress target : broadcasts) {
                            try {socket.send(new DatagramPacket(request, request.length, target, 9595));} catch (IOException ignored) {}
                        }
                        long end = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(800);
                        while (System.nanoTime() < end) {
                            try {
                                byte[] data = new byte[2048]; DatagramPacket response = new DatagramPacket(data, data.length);
                                socket.receive(response);
                                JSONObject body = new JSONObject(new String(data, 0, response.getLength(), StandardCharsets.UTF_8));
                                String host = response.getAddress().getHostAddress();
                                if ("ai-karaoke".equals(body.optString("service")) && body.optInt("version") == 1) candidates.add(host);
                            } catch (SocketTimeoutException ignored) {} catch (org.json.JSONException ignored) {}
                        }
                    }
                }
                ExecutorService probes = Executors.newFixedThreadPool(32);
                try {
                    for (String host : candidates) probes.submit(() -> probe(host, found));
                    probes.shutdown();
                    probes.awaitTermination(12, TimeUnit.SECONDS);
                } finally {probes.shutdownNow();}
                List<JSObject> sorted = new ArrayList<>(found.values());
                sorted.sort(Comparator.comparing(o -> o.optString("host")));
                JSObject result = new JSObject(); result.put("servers", new JSArray(sorted)); call.resolve(result);
            } catch (Exception e) {call.reject("Не удалось найти серверы: " + e.getMessage(), e);}
            finally {scanning.set(false);}
        });
    }

    private void probe(String host, Map<String, JSObject> found) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL("http://" + host + ":9595/api/health").openConnection();
            connection.setConnectTimeout(350); connection.setReadTimeout(500); connection.setInstanceFollowRedirects(false);
            if (connection.getResponseCode() != 200) return;
            try (InputStream input = connection.getInputStream()) {
                ByteArrayOutputStream output = new ByteArrayOutputStream(); byte[] buffer = new byte[1024]; int count;
                while ((count = input.read(buffer)) != -1) {output.write(buffer, 0, count); if (output.size() > 8192) return;}
                JSObject body = new JSObject(output.toString("UTF-8"));
                if (!"ai-karaoke".equals(body.optString("service")) || body.optInt("version") != 1) return;
                body.put("host", host); found.put(host, body);
            }
        } catch (Exception ignored) {} finally {if (connection != null) connection.disconnect();}
    }

    @PluginMethod
    public void keepAwake(PluginCall call) {
        boolean enabled = Boolean.TRUE.equals(call.getBoolean("enabled", false));
        getActivity().runOnUiThread(() -> {
            if (enabled) getActivity().getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else getActivity().getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            call.resolve();
        });
    }

    @PluginMethod
    public void download(PluginCall call) {
        if (Build.VERSION.SDK_INT <= 28 && getPermissionState("downloads") != PermissionState.GRANTED) {
            requestPermissionForAlias("downloads", call, "downloadPermission"); return;
        }
        enqueueDownload(call);
    }

    @PermissionCallback
    private void downloadPermission(PluginCall call) {
        if (getPermissionState("downloads") == PermissionState.GRANTED) enqueueDownload(call);
        else call.reject("Разрешите сохранение файлов, чтобы скачать экспорт");
    }

    private void enqueueDownload(PluginCall call) {
        try {
            Uri uri = Uri.parse(call.getString("url", ""));
            if (!"http".equals(uri.getScheme()) || uri.getPort() != 9595 || uri.getHost() == null || !uri.getPath().startsWith("/api/download/"))
                throw new IllegalArgumentException("Недопустимый адрес загрузки");
            String filename = call.getString("filename", "karaoke.mp3").replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
            DownloadManager.Request request = new DownloadManager.Request(uri)
                .setTitle(filename).setDescription("AI Karaoke")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
                .setAllowedOverMetered(true);
            DownloadManager manager = (DownloadManager) getContext().getSystemService(Context.DOWNLOAD_SERVICE);
            long id = manager.enqueue(request);
            JSObject result = new JSObject(); result.put("downloadId", id); call.resolve(result);
        } catch (Exception e) {call.reject("Не удалось сохранить файл: " + e.getMessage(), e);}
    }

    @Override protected void handleOnDestroy() {worker.shutdownNow();}
}
