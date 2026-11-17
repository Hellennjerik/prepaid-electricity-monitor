package com.hellennjerik.prepaid_electricity_monitor

import android.app.Application

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        SessionStore.init(this)
        ApiClient.init(this)
    }
}
