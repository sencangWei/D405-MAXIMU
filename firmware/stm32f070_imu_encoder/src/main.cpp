#include <array>
#include <cstddef>
#include <cstdint>

#include "stm32f0xx.h"

#include "as5047p_protocol.h"
#include "capture_pipeline.h"
#include "combined_packet.h"

namespace {

constexpr uint32_t kSystemClockHz = 48000000U;
constexpr uint32_t kUartBaud = 921600U;
constexpr uint32_t kUartBrr = (kSystemClockHz + kUartBaud / 2U) / kUartBaud;
constexpr uint8_t kEncoderCsPin = 4U;
constexpr size_t kTxQueueCapacity = 4U;

using Packet = std::array<uint8_t, combined::kPacketSize>;

capture::Pipeline g_pipeline;
capture::ImuRxBuffer<64U> g_imu_rx;
volatile uint32_t g_timer_high = 0U;
uint32_t g_consumed_rx_generation = 0U;

class UartTxQueue {
public:
    bool hasSpace() const {
        return increment(head_) != tail_;
    }

    bool enqueue(const Packet& packet) {
        const uint8_t next = increment(head_);
        if (next == tail_) {
            return false;
        }
        packets_[head_] = packet;
        __DMB();
        head_ = next;
        USART1->CR1 |= USART_CR1_TXEIE;
        return true;
    }

    void handleInterrupt() {
        if ((USART1->ISR & USART_ISR_TXE) == 0U ||
            (USART1->CR1 & USART_CR1_TXEIE) == 0U) {
            return;
        }
        if (tail_ == head_) {
            USART1->CR1 &= ~USART_CR1_TXEIE;
            return;
        }

        USART1->TDR = packets_[tail_][offset_++];
        if (offset_ == combined::kPacketSize) {
            offset_ = 0U;
            tail_ = increment(tail_);
            if (tail_ == head_) {
                USART1->CR1 &= ~USART_CR1_TXEIE;
            }
        }
    }

private:
    static constexpr uint8_t kStorageSize =
        static_cast<uint8_t>(kTxQueueCapacity + 1U);

    static uint8_t increment(uint8_t index) {
        ++index;
        return index == kStorageSize ? 0U : index;
    }

    std::array<Packet, kTxQueueCapacity + 1U> packets_{};
    volatile uint8_t head_ = 0U;
    volatile uint8_t tail_ = 0U;
    uint8_t offset_ = 0U;
};

UartTxQueue g_uart_tx;

void configureSystemClock() {
    RCC->CR |= RCC_CR_HSION;
    while ((RCC->CR & RCC_CR_HSIRDY) == 0U) {
    }

    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY;
    RCC->CFGR &= ~(RCC_CFGR_PLLSRC | RCC_CFGR_PLLMUL |
                   RCC_CFGR_HPRE | RCC_CFGR_PPRE | RCC_CFGR_SW);
    RCC->CFGR |= RCC_CFGR_PLLSRC_HSI_DIV2 | RCC_CFGR_PLLMUL12;
    RCC->CR |= RCC_CR_PLLON;
    while ((RCC->CR & RCC_CR_PLLRDY) == 0U) {
    }

    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) {
    }
    SystemCoreClock = kSystemClockHz;
}

void setAlternateFunction(uint8_t pin, uint8_t alternate) {
    const uint8_t register_index = pin / 8U;
    const uint8_t shift = static_cast<uint8_t>((pin % 8U) * 4U);
    GPIOA->AFR[register_index] =
        (GPIOA->AFR[register_index] & ~(0xFU << shift)) |
        (static_cast<uint32_t>(alternate) << shift);
}

void setMode(uint8_t pin, uint32_t mode) {
    const uint8_t shift = static_cast<uint8_t>(pin * 2U);
    GPIOA->MODER = (GPIOA->MODER & ~(0x3U << shift)) | (mode << shift);
}

void configureGpio() {
    RCC->AHBENR |= RCC_AHBENR_GPIOAEN;

    setMode(2U, 0x2U);
    setMode(3U, 0x2U);
    setMode(4U, 0x1U);
    setMode(5U, 0x2U);
    setMode(6U, 0x2U);
    setMode(7U, 0x2U);
    setMode(9U, 0x2U);
    setMode(10U, 0x2U);

    setAlternateFunction(2U, 1U);
    setAlternateFunction(3U, 1U);
    setAlternateFunction(5U, 0U);
    setAlternateFunction(6U, 0U);
    setAlternateFunction(7U, 0U);
    setAlternateFunction(9U, 1U);
    setAlternateFunction(10U, 1U);

    const uint32_t high_speed_pins =
        (0x3U << (2U * 2U)) | (0x3U << (3U * 2U)) |
        (0x3U << (4U * 2U)) | (0x3U << (5U * 2U)) |
        (0x3U << (6U * 2U)) | (0x3U << (7U * 2U)) |
        (0x3U << (9U * 2U)) | (0x3U << (10U * 2U));
    GPIOA->OSPEEDR |= high_speed_pins;
    GPIOA->PUPDR = (GPIOA->PUPDR & ~(0x3U << (6U * 2U))) |
                   (0x1U << (6U * 2U));
    GPIOA->BSRR = 1U << kEncoderCsPin;
}

void configureMicrosecondTimer() {
    RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
    TIM3->PSC = 47U;
    TIM3->ARR = 0xFFFFU;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->SR = 0U;
    TIM3->DIER = TIM_DIER_UIE;
    NVIC_SetPriority(TIM3_IRQn, 0U);
    NVIC_EnableIRQ(TIM3_IRQn);
    TIM3->CR1 = TIM_CR1_CEN;
}

uint32_t micros32() {
    uint32_t high_before;
    uint32_t high_after;
    uint16_t low;
    do {
        high_before = g_timer_high;
        __DMB();
        low = static_cast<uint16_t>(TIM3->CNT);
        __DMB();
        high_after = g_timer_high;
    } while (high_before != high_after);
    return (high_before << 16U) | low;
}

void configureUarts() {
    RCC->APB2ENR |= RCC_APB2ENR_USART1EN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;

    USART1->BRR = kUartBrr;
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
    USART1->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                  USART_ICR_NCF | USART_ICR_PECF;
    NVIC_SetPriority(USART1_IRQn, 2U);
    NVIC_EnableIRQ(USART1_IRQn);

    USART2->BRR = kUartBrr;
    USART2->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                  USART_ICR_NCF | USART_ICR_PECF;
    USART2->CR3 = USART_CR3_EIE;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE |
                  USART_CR1_RXNEIE | USART_CR1_UE;
    NVIC_SetPriority(USART2_IRQn, 1U);
    NVIC_EnableIRQ(USART2_IRQn);
}

void configureSpi() {
    RCC->APB2ENR |= RCC_APB2ENR_SPI1EN;
    SPI1->CR1 = SPI_CR1_MSTR | SPI_CR1_CPHA | SPI_CR1_SSM |
                SPI_CR1_SSI | SPI_CR1_BR_2 | SPI_CR1_BR_0;
    SPI1->CR2 = SPI_CR2_DS;
    SPI1->CR1 |= SPI_CR1_SPE;
}

uint16_t spiTransfer16(uint16_t value) {
    while ((SPI1->SR & SPI_SR_TXE) == 0U) {
    }
    *reinterpret_cast<volatile uint16_t*>(&SPI1->DR) = value;
    while ((SPI1->SR & SPI_SR_RXNE) == 0U) {
    }
    const uint16_t response =
        *reinterpret_cast<volatile uint16_t*>(&SPI1->DR);
    while ((SPI1->SR & SPI_SR_BSY) != 0U) {
    }
    return response;
}

void delayMicroseconds(uint32_t delay_us) {
    const uint32_t start = micros32();
    while (static_cast<uint32_t>(micros32() - start) < delay_us) {
    }
}

uint16_t encoderTransaction(uint16_t command) {
    GPIOA->BRR = 1U << kEncoderCsPin;
    delayMicroseconds(1U);
    const uint16_t response = spiTransfer16(command);
    GPIOA->BSRR = 1U << kEncoderCsPin;
    delayMicroseconds(1U);
    return response;
}

uint16_t readEncoder() {
    encoderTransaction(
        as5047p::makeReadCommand(as5047p::kAngleUncompensatedAddress));
    return encoderTransaction(as5047p::makeNopCommand());
}

void serviceCapturePipeline() {
    capture::ImuRxByte input{};
    while (g_imu_rx.pop(input)) {
        if (input.generation != g_consumed_rx_generation) {
            g_pipeline.abortImuFrame();
            g_pipeline.noteImuQueueOverflow(
                input.generation - g_consumed_rx_generation);
            g_consumed_rx_generation = input.generation;
        }
        const auto event = g_pipeline.onImuByte(input.value, input.rx_us);
        if (event == capture::PipelineEvent::EncoderReadRequested) {
            const uint32_t encoder_read_us = micros32();
            const uint16_t response = readEncoder();
            g_pipeline.storePendingEncoder(response, encoder_read_us);
        }
    }

    while (g_uart_tx.hasSpace()) {
        combined::Sample sample{};
        if (!g_pipeline.popOutput(sample)) {
            break;
        }
        if (!g_uart_tx.enqueue(combined::encode(sample))) {
            break;
        }
    }
}

}  // namespace

extern "C" void TIM3_IRQHandler() {
    if ((TIM3->SR & TIM_SR_UIF) != 0U) {
        TIM3->SR &= ~TIM_SR_UIF;
        ++g_timer_high;
    }
}

extern "C" void USART1_IRQHandler() {
    g_uart_tx.handleInterrupt();
}

extern "C" void USART2_IRQHandler() {
    const uint32_t status = USART2->ISR;
    const uint32_t rx_us = micros32();
    const bool corrupted_byte =
        (status & (USART_ISR_FE | USART_ISR_NE | USART_ISR_PE)) != 0U;

    if ((status & (USART_ISR_ORE | USART_ISR_FE |
                   USART_ISR_NE | USART_ISR_PE)) != 0U) {
        g_imu_rx.noteDiscontinuity();
        USART2->ICR = USART_ICR_ORECF | USART_ICR_FECF |
                      USART_ICR_NCF | USART_ICR_PECF;
    }
    if ((status & USART_ISR_RXNE) != 0U) {
        const uint8_t byte = static_cast<uint8_t>(USART2->RDR);
        if (!corrupted_byte) {
            g_imu_rx.push({byte, rx_us});
        }
    }
}

int main() {
    configureSystemClock();
    configureGpio();
    configureMicrosecondTimer();
    configureUarts();
    configureSpi();

    while (true) {
        serviceCapturePipeline();
        __disable_irq();
        const bool work_ready = !g_imu_rx.empty() ||
                                (g_pipeline.hasOutput() &&
                                 g_uart_tx.hasSpace());
        if (!work_ready) {
            __DSB();
            __WFI();
        }
        __enable_irq();
    }
}
