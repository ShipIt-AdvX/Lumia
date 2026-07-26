/**
 * @file wav_encode.cpp
 */
#include "wav_encode.h"
#include <string.h>

typedef unsigned char ID[4];

typedef struct {
    ID RIFF;
    uint8_t size[4];
    ID CDDA;
    ID fmt;
    uint8_t chunkSize[4];
    uint8_t wFormatTag[2];
    uint8_t wChannels[2];
    uint8_t dwSamplesPerSec[4];
    uint8_t dwAvgBytesPerSec[4];
    uint8_t wBlockAlign[2];
    uint8_t wBitsPerSample[2];
    uint8_t dataID[4];
    uint8_t dataSize[4];
} __attribute__((packed)) WAVE_HEAD_FMT;

OPERATE_RET app_get_wav_head(uint32_t pcm_len, uint8_t cd_format, uint32_t sample_rate,
                             uint16_t bit_depth, uint16_t channel, uint8_t head[])
{
    if (NULL == head || 0 == pcm_len || cd_format != 1) {
        return OPRT_INVALID_PARM;
    }

    uint32_t total_len = pcm_len + 36;
    uint32_t byte_rate = sample_rate * channel * bit_depth / 8;
    uint32_t block_align = channel * bit_depth / 8;
    WAVE_HEAD_FMT *w = (WAVE_HEAD_FMT *)head;

    memcpy(w->RIFF, "RIFF", 4);
    w->size[0] = total_len & 0xff;
    w->size[1] = (total_len >> 8) & 0xff;
    w->size[2] = (total_len >> 16) & 0xff;
    w->size[3] = (total_len >> 24) & 0xff;
    memcpy(w->CDDA, "WAVE", 4);
    memcpy(w->fmt, "fmt ", 4);
    w->chunkSize[0] = 16;
    w->chunkSize[1] = w->chunkSize[2] = w->chunkSize[3] = 0;
    w->wFormatTag[0] = cd_format;
    w->wFormatTag[1] = 0;
    w->wChannels[0] = channel;
    w->wChannels[1] = 0;
    w->dwSamplesPerSec[0] = sample_rate & 0xff;
    w->dwSamplesPerSec[1] = (sample_rate >> 8) & 0xff;
    w->dwSamplesPerSec[2] = (sample_rate >> 16) & 0xff;
    w->dwSamplesPerSec[3] = (sample_rate >> 24) & 0xff;
    w->dwAvgBytesPerSec[0] = byte_rate & 0xff;
    w->dwAvgBytesPerSec[1] = (byte_rate >> 8) & 0xff;
    w->dwAvgBytesPerSec[2] = (byte_rate >> 16) & 0xff;
    w->dwAvgBytesPerSec[3] = (byte_rate >> 24) & 0xff;
    w->wBlockAlign[0] = block_align & 0xff;
    w->wBlockAlign[1] = (block_align >> 8) & 0xff;
    w->wBitsPerSample[0] = bit_depth & 0xff;
    w->wBitsPerSample[1] = (bit_depth >> 8) & 0xff;
    memcpy(w->dataID, "data", 4);
    w->dataSize[0] = pcm_len & 0xff;
    w->dataSize[1] = (pcm_len >> 8) & 0xff;
    w->dataSize[2] = (pcm_len >> 16) & 0xff;
    w->dataSize[3] = (pcm_len >> 24) & 0xff;
    return OPRT_OK;
}
