package com.hellennjerik.prepaid_electricity_monitor

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [User::class, MeterReading::class, TokenPurchase::class, UserSetting::class, AlertLog::class],
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun userDao(): UserDao
    abstract fun meterReadingDao(): MeterReadingDao
    abstract fun tokenPurchaseDao(): TokenPurchaseDao
    abstract fun userSettingDao(): UserSettingDao
    abstract fun alertLogDao(): AlertLogDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "prepaid_electricity_db"
                ).build()
                INSTANCE = instance
                instance
            }
        }
    }
}