/**
 * @file wav_encode.h
 */
#ifndef __WAV_ENCODE_H__
#define __WAV_ENCODE_H__

#include "tuya_cloud_types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define WAV_HEAD_LEN 44

OPERATE_RET app_get_wav_head(uint32_t pcm_len, uint8_t cd_format, uint32_t sample_rate,
                             uint16_t bit_depth, uint16_t channel, uint8_t head[]);

#ifdef __cplusplus
}
#endif

#endif
