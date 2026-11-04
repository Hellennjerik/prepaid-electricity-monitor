package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import com.google.android.material.materialswitch.MaterialSwitch

class ProfileActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_profile)

        val switchDarkMode = findViewById<MaterialSwitch>(R.id.switchDarkMode)

        // 1. Check what mode the app is currently in
        val sharedPreferences = getSharedPreferences("AppSettings", Context.MODE_PRIVATE)
        val isDarkMode = sharedPreferences.getBoolean("isDarkMode", true) // Default to true
        switchDarkMode.isChecked = isDarkMode

        // 2. Listen for changes to the switch
        switchDarkMode.setOnCheckedChangeListener { _, isChecked ->
            // Save the preference
            sharedPreferences.edit().putBoolean("isDarkMode", isChecked).apply()

            // Apply the theme instantly
            if (isChecked) {
                AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
            } else {
                AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
            }
        }
    }
}