package dev.livachat.app.ui.screens

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import dev.livachat.app.data.local.PreferencesDataSource
import dev.livachat.app.ui.screens.splash.SplashDestination
import dev.livachat.app.ui.screens.splash.SplashViewModel
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class SplashViewModelTest {

    private val dataSource = mockk<PreferencesDataSource>(relaxed = true)
    // relaxed mock 的 checkSelfPermission 默认返回 0 = PERMISSION_GRANTED，不影响既有断言
    private val context = mockk<Context>(relaxed = true)

    // 与 ConversationViewModel*Test 同款：Main 用虚拟时钟 TestDispatcher
    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun destination_is_null_before_bootstrap() {
        every { dataSource.isFirstLaunch } returns flowOf(true)
        val vm = SplashViewModel(dataSource, context)
        assertNull(vm.destination.value)
    }

    @Test
    fun initialState_is_correct() {
        every { dataSource.isFirstLaunch } returns flowOf(true)
        val vm = SplashViewModel(dataSource, context)
        assertEquals(null, vm.destination.value)
    }

    @Test
    fun mic_denied_routes_to_permission_even_for_returning_user() {
        // 老用户（isFirstLaunch=false）但系统已回收 mic 权限 → 门禁仍拦截到授权页
        every { dataSource.isFirstLaunch } returns flowOf(false)
        every { context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) }
            .returns(PackageManager.PERMISSION_DENIED)
        val vm = SplashViewModel(dataSource, context)
        vm.bootstrap()
        // bootstrap 内含 1s 最小展示 delay：推进虚拟时钟越过它
        dispatcher.scheduler.advanceUntilIdle()
        assertEquals(SplashDestination.Permission, vm.destination.value)
    }
}
