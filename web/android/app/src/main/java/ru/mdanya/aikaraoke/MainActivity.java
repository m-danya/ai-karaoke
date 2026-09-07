package ru.mdanya.aikaraoke;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(KaraokeLanPlugin.class);
        super.onCreate(savedInstanceState);
        setVolumeControlStream(android.media.AudioManager.STREAM_MUSIC);
    }
}
