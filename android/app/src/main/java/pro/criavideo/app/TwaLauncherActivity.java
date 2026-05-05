package pro.criavideo.app;

import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

import com.google.androidbrowserhelper.trusted.LauncherActivity;

public class TwaLauncherActivity extends LauncherActivity {
    private static final String FALLBACK_URL = "https://criavideo.pro/video";

    @Override
    protected boolean shouldLaunchImmediately() {
        return false;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (isFinishing()) {
            return;
        }
        try {
            launchTwa();
        } catch (RuntimeException exception) {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(FALLBACK_URL));
            intent.addCategory(Intent.CATEGORY_BROWSABLE);
            startActivity(intent);
            finish();
        }
    }
}