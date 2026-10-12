package com.hellennjerik.prepaid_electricity_monitor

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query

@Dao
interface UserSettingDao {
    @Insert
    suspend fun insertSettings(settings: UserSetting)

    @Query("SELECT * FROM user_settings WHERE userId = :userId LIMIT 1")
    suspend fun getSettings(userId: Int): UserSetting?
}